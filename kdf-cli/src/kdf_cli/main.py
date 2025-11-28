#!/usr/bin/env python3
"""kdf: Kernel development flake - Manage kdf-init initramfs and kernel execution."""

import argparse
import logging
import subprocess
import sys
from pathlib import Path

from kdf_cli.bg_tasks import BackgroundTaskManager
from kdf_cli.initramfs import (
    copy_file,
    create_initramfs_archive,
    get_prebuilt_init,
    get_prebuilt_initramfs,
)
from kdf_cli.nix import resolve_kernel_and_initramfs
from kdf_cli.qemu import QemuCommand
from kdf_cli.virtiofs import VirtiofsError, create_virtiofs_tasks

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("kdf.log"), logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("kdf")


def cmd_build_initramfs(args: argparse.Namespace) -> None:
    """Build initramfs cpio archive from init binary."""
    try:
        # Parse module paths if provided
        modules = []
        if args.modules:
            for module_path_str in args.modules:
                module_path = Path(module_path_str)
                modules.append(module_path)

        # Determine output path
        output_path = args.output if args.output else Path("./initramfs.cpio")

        # Special case: No modules and no custom init
        # Just copy prebuilt initramfs if available
        if not modules and args.init_binary is None:
            prebuilt_initramfs = get_prebuilt_initramfs()
            if prebuilt_initramfs is not None:
                logger.info("Copying prebuilt initramfs to: %s", output_path)
                copy_file(prebuilt_initramfs, output_path)
                return

        # Determine which init binary to use
        if args.init_binary is None:
            # Try to use prebuilt init
            prebuilt_init = get_prebuilt_init()
            if prebuilt_init is None:
                msg = (
                    "No init binary specified and no prebuilt init available. "
                    "Please provide an init binary as the first argument."
                )
                raise FileNotFoundError(
                    msg,
                )
            logger.info("Using prebuilt init binary: %s", prebuilt_init)
            init_binary = prebuilt_init
        else:
            if not args.init_binary.exists():
                msg = f"Init binary not found: {args.init_binary}"
                raise FileNotFoundError(msg)
            init_binary = args.init_binary

        # Build initramfs directly to output
        create_initramfs_archive(init_binary, output_path, modules, args.moddir)
        logger.info("Created initramfs: %s", output_path)
    except Exception as e:
        logger.exception("Error: %s", e)
        sys.exit(1)


def _resolve_kernel_and_initramfs(args: argparse.Namespace) -> tuple[Path, Path]:
    """Resolve kernel and initramfs paths from args.

    Args:
        args: Parsed command-line arguments

    Returns:
        Tuple of (kernel_path, initramfs_path)

    """
    if args.release is not None:
        try:
            kernel, initramfs = resolve_kernel_and_initramfs(
                version=args.release if args.release else None,
                custom_initramfs=args.initramfs,
            )
            logger.info("Resolved kernel: %s", kernel)
            logger.info("Resolved initramfs: %s", initramfs)
            return kernel, initramfs
        except Exception as e:
            logger.exception("Failed to resolve kernel from nixpkgs: %s", e)
            sys.exit(1)

    # Use provided kernel
    kernel = args.kernel
    if not kernel.exists():
        logger.error("Kernel not found: %s", kernel)
        sys.exit(1)

    # Determine which initramfs to use
    initramfs: Path
    if args.initramfs is not None:
        initramfs = args.initramfs
    else:
        # Try to use prebuilt initramfs
        prebuilt_initramfs = get_prebuilt_initramfs()
        if prebuilt_initramfs is None:
            logger.error(
                "No initramfs specified and no prebuilt initramfs available. "
                "Please provide --initramfs or build kdf-cli from the Nix package.",
            )
            sys.exit(1)
        # TODO: ty doesn't understand sys.exit(1) never returns
        # so it can't narrow the type
        initramfs = prebuilt_initramfs  # type: ignore[invalid-assignment]
        logger.info("Using prebuilt initramfs: %s", initramfs)

    if not initramfs.exists():
        logger.error("Initramfs not found: %s", initramfs)
        sys.exit(1)

    return kernel, initramfs


def _configure_init(args: argparse.Namespace, qemu_cmd: QemuCommand) -> None:
    """Configure init settings from command-line arguments.

    Args:
        args: Parsed command-line arguments
        qemu_cmd: QEMU command builder to configure

    """
    # Set moddir for kernel module loading
    if args.moddir:
        qemu_cmd.init_config.moddir = args.moddir

    # Set environment variables
    if args.env_vars:
        for env_spec in args.env_vars:
            if "=" not in env_spec:
                logger.error(
                    "Invalid environment variable format: %s (expected KEY=VALUE)",
                    env_spec,
                )
                sys.exit(1)
            key, value = env_spec.split("=", 1)
            qemu_cmd.init_config.env_vars[key] = value

    # Handle --nix packages for PATH
    if args.nix is not None and args.nix:
        # Parse comma-separated package list
        package_attrs = [pkg.strip() for pkg in args.nix.split(",") if pkg.strip()]
        if package_attrs:
            from kdf_cli.nix import resolve_nix_packages

            bin_path = resolve_nix_packages(package_attrs)
            # Append to existing PATH if present
            existing_path = qemu_cmd.init_config.env_vars.get("PATH", "")
            if existing_path:
                qemu_cmd.init_config.env_vars["PATH"] = f"{existing_path}:{bin_path}"
            else:
                qemu_cmd.init_config.env_vars["PATH"] = bin_path
            logger.info("Added Nix packages to PATH: %s", package_attrs)

    # Set shell (always set from args, with default from argparse)
    qemu_cmd.init_config.shell = args.shell

    # Set script (optional)
    if args.script:
        qemu_cmd.init_config.script = args.script

    # Set chdir (optional)
    if args.chdir:
        qemu_cmd.init_config.chdir = args.chdir


def cmd_run(args: argparse.Namespace) -> None:
    """Run QEMU with kernel and initramfs."""
    kernel, initramfs = _resolve_kernel_and_initramfs(args)

    # Create background task manager
    task_manager = BackgroundTaskManager()

    try:
        # Handle --nix option
        virtiofs_shares = list(args.virtiofs) if args.virtiofs else []
        if args.nix is not None:
            # Check if /nix/store virtiofs already exists
            has_nixstore = any(
                share.startswith("nixstore:") or ":/nix/store:" in share
                for share in virtiofs_shares
            )
            if not has_nixstore:
                # Use 'always' cache mode for /nix/store since it's
                # read-only and immutable
                virtiofs_shares.append("nixstore:/nix/store:/nix/store::always")
                logger.info("Adding /nix/store virtiofs mount with 'always' cache")

        # Create virtiofs tasks (but don't start yet)
        if virtiofs_shares:
            create_virtiofs_tasks(virtiofs_shares, task_manager)

        # Start all background tasks
        task_manager.start_all()

        # Build QEMU command
        qemu_cmd = QemuCommand(kernel, initramfs, args.memory)

        # Register all tasks with QEMU (adds runtime info like sockets)
        task_manager.register_all_with_qemu(qemu_cmd)

        # Configure init settings
        _configure_init(args, qemu_cmd)

        # Add additional cmdline
        if args.cmdline:
            qemu_cmd.add_cmdline(args.cmdline)

        # Build and run command
        cmd = qemu_cmd.build()
        logger.info("Running QEMU with command:")
        logger.info(" ".join(cmd))
        subprocess.run(cmd, check=False)
    except (ValueError, VirtiofsError) as e:
        logger.exception("Error: %s", e)
        sys.exit(1)
    finally:
        task_manager.cleanup()


def main() -> None:
    """Run the kdf CLI."""
    parser = argparse.ArgumentParser(
        prog="kdf",
        description="kdf: Kernel development flake tools",
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommands")

    # build initramfs subcommand
    build_parser = subparsers.add_parser("build", help="Build subcommands")
    build_subparsers = build_parser.add_subparsers(dest="build_command")

    initramfs_parser = build_subparsers.add_parser(
        "initramfs",
        help="Build initramfs cpio archive",
    )
    initramfs_parser.add_argument(
        "init_binary",
        type=Path,
        nargs="?",
        default=None,
        help="Path to init binary (default: use prebuilt kdf-init if available)",
    )
    initramfs_parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Output cpio file (default: ./initramfs.cpio)",
    )
    initramfs_parser.add_argument(
        "--module",
        "-m",
        action="append",
        dest="modules",
        help="Kernel module to include (can be specified multiple times)",
    )
    initramfs_parser.add_argument(
        "--moddir",
        default="/init-modules",
        help="Directory to store modules in initramfs (default: /init-modules)",
    )

    # run subcommand
    run_parser = subparsers.add_parser("run", help="Run kernel with initramfs in QEMU")
    kernel_group = run_parser.add_mutually_exclusive_group(required=True)
    kernel_group.add_argument("--kernel", type=Path, help="Path to kernel image")
    kernel_group.add_argument(
        "--release",
        "-r",
        nargs="?",
        const="",
        metavar="VERSION",
        help=(
            "Use nixpkgs kernel release "
            "(optionally specify version, defaults to system kernel)"
        ),
    )
    run_parser.add_argument(
        "--initramfs",
        type=Path,
        default=None,
        help="Path to initramfs cpio (default: use prebuilt if available)",
    )
    run_parser.add_argument(
        "--virtiofs",
        "-v",
        action="append",
        help=(
            "Virtiofs share: tag:host_path:guest_path[:overlay][:cache] "
            "where overlay is 'overlay' or empty, and cache is "
            "none/auto/always (default: auto). "
            "Use :: to skip overlay field, e.g., tag:path:path::none"
        ),
    )
    run_parser.add_argument(
        "--cmdline",
        default="",
        help="Additional kernel cmdline arguments",
    )
    run_parser.add_argument(
        "--memory",
        "-m",
        default="512M",
        help="QEMU memory (default: 512M)",
    )
    run_parser.add_argument(
        "--moddir",
        default="/init-modules",
        help="Directory to load kernel modules from (default: /init-modules)",
    )
    run_parser.add_argument(
        "--env",
        "-e",
        action="append",
        dest="env_vars",
        help=(
            "Environment variable to set in VM: KEY=VALUE "
            "(can be specified multiple times)"
        ),
    )
    run_parser.add_argument(
        "--nix",
        nargs="?",
        const="",
        metavar="PKG1,PKG2,...",
        help=(
            "Mount /nix/store as read-only virtiofs. "
            "Optionally provide comma-separated package names to add to PATH "
            "(e.g., --nix busybox,coreutils)"
        ),
    )
    run_parser.add_argument(
        "--chdir",
        metavar="DIR",
        help="Change to directory DIR before spawning shell",
    )

    # Shell is always used, script is optional
    run_parser.add_argument(
        "--shell",
        "-s",
        default="sh -i",
        help="Shell to start in VM (default: sh -i)",
    )
    run_parser.add_argument(
        "--script",
        "-c",
        help="Script to execute in VM (not yet implemented)",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "build":
        if not args.build_command:
            build_parser.print_help()
            sys.exit(1)
        if args.build_command == "initramfs":
            cmd_build_initramfs(args)
    elif args.command == "run":
        cmd_run(args)


if __name__ == "__main__":
    main()
