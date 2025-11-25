#!/usr/bin/env python3
"""kdf: Kernel development flake - Manage kdf-init initramfs and kernel execution"""

import argparse
import hashlib
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from kdf_cli.bg_tasks import BackgroundTaskManager
from kdf_cli.qemu import QemuCommand
from kdf_cli.virtiofs import VirtiofsError, create_virtiofs_tasks

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('kdf.log'),
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger('kdf')


def get_cache_dir() -> Path:
    """Get XDG cache directory for kdf"""
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache:
        cache_dir = Path(xdg_cache) / "kdf"
    else:
        cache_dir = Path.home() / ".cache" / "kdf"

    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def hash_file(path: Path) -> str:
    """Calculate SHA256 hash of file"""
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()


def copy_file(src: Path, dst: Path) -> None:
    """Copy file from src to dst"""
    subprocess.run(["cp", str(src), str(dst)], check=True)


def get_cached_initramfs(binary_hash: str) -> Path:
    """Get path to cached initramfs for given binary hash"""
    cache_dir = get_cache_dir()
    return cache_dir / f"initramfs-{binary_hash}.cpio"


def get_module_dependencies(module_path: Path) -> list[str]:
    """Get module dependencies using modinfo"""
    try:
        result = subprocess.run(
            ["modinfo", "-F", "depends", str(module_path)],
            capture_output=True,
            text=True,
            check=True
        )
        deps = result.stdout.strip()
        if deps:
            return [d.strip() for d in deps.split(',') if d.strip()]
        return []
    except subprocess.CalledProcessError:
        return []

def topological_sort_modules(modules: list[Path]) -> list[Path]:
    """Sort modules in dependency order using topological sort"""
    # Build dependency graph
    module_map = {}  # name (without .ko.xz) -> Path
    dependencies = {}  # name -> list of dependency names

    for module_path in modules:
        name = module_path.name
        # Remove compression extensions
        if name.endswith('.xz'):
            name = name[:-3]
        if name.endswith('.gz'):
            name = name[:-3]
        # Remove .ko extension
        if name.endswith('.ko'):
            name = name[:-3]

        module_map[name] = module_path
        dependencies[name] = get_module_dependencies(module_path)

    # Topological sort
    sorted_modules = []
    visited = set()

    def visit(name: str):
        if name in visited:
            return
        visited.add(name)

        # Visit dependencies first
        for dep in dependencies.get(name, []):
            if dep in module_map:  # Only if we have this dependency
                visit(dep)

        if name in module_map:
            sorted_modules.append(module_map[name])

    # Visit all modules
    for name in module_map:
        visit(name)

    return sorted_modules

def create_initramfs_archive(init_binary: Path, output_path: Path, modules: list[Path], moddir: str) -> None:
    """Create initramfs cpio archive from init binary and optional kernel modules"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        # Copy init binary to temp directory
        init_path = tmppath / "init"
        copy_file(init_binary, init_path)
        subprocess.run(["chmod", "+x", str(init_path)], check=True)

        # Copy kernel modules if provided
        if modules:
            # Sort modules by dependencies
            sorted_modules = topological_sort_modules(modules)
            logger.info("Module load order after dependency resolution:")
            for idx, mod in enumerate(sorted_modules, 1):
                logger.info(f"  {idx}. {mod.name}")

            # Strip leading slash for creating directory in tmpdir
            moddir_relative = moddir.lstrip("/")
            modules_dir = tmppath / moddir_relative
            modules_dir.mkdir(parents=True, exist_ok=True)

            for idx, module_path in enumerate(sorted_modules):
                if not module_path.exists():
                    raise FileNotFoundError(f"Kernel module not found: {module_path}")

                # Decompress if needed and add numeric prefix for load order
                module_name = module_path.name
                prefix = f"{idx:02d}-"  # Two-digit prefix: 00-, 01-, etc.

                if module_name.endswith('.xz'):
                    # Decompress .xz module
                    decompressed_name = module_name[:-3]  # Remove .xz extension
                    final_name = prefix + decompressed_name
                    module_dest = modules_dir / final_name
                    subprocess.run(["xz", "-dc", str(module_path)], stdout=open(module_dest, "wb"), check=True)
                    logger.info(f"Added module: {module_name} -> {final_name}")
                elif module_name.endswith('.gz'):
                    # Decompress .gz module
                    decompressed_name = module_name[:-3]  # Remove .gz extension
                    final_name = prefix + decompressed_name
                    module_dest = modules_dir / final_name
                    subprocess.run(["gzip", "-dc", str(module_path)], stdout=open(module_dest, "wb"), check=True)
                    logger.info(f"Added module: {module_name} -> {final_name}")
                else:
                    # Copy as-is
                    final_name = prefix + module_name
                    module_dest = modules_dir / final_name
                    copy_file(module_path, module_dest)
                    logger.info(f"Added module: {module_name} -> {final_name}")

        # Create cpio archive
        with open(output_path, "wb") as f:
            subprocess.run(
                "find . -print0 | cpio --null -o -H newc",
                cwd=tmpdir,
                shell=True,
                stdout=f,
                check=True,
            )


def cmd_build_initramfs(args):
    """Build initramfs cpio archive from init binary"""
    try:
        if not args.init_binary.exists():
            raise FileNotFoundError(f"Init binary not found: {args.init_binary}")

        # Parse module paths if provided
        modules = []
        if args.modules:
            for module_path_str in args.modules:
                module_path = Path(module_path_str)
                modules.append(module_path)

        # Determine output path
        output_path = args.output if args.output else Path("./initramfs.cpio")

        # Check cache if enabled (skip cache if modules are included)
        if not args.no_cache and not modules:
            binary_hash = hash_file(args.init_binary)
            cached_path = get_cached_initramfs(binary_hash)

            if cached_path.exists():
                logger.info(f"Using cached initramfs: {cached_path}")
                copy_file(cached_path, output_path)
                logger.info(f"Copied to: {output_path}")
                return

            # Cache miss: build to cache, then copy to output
            create_initramfs_archive(args.init_binary, cached_path, modules, args.moddir)
            logger.info(f"Created initramfs: {cached_path}")

            copy_file(cached_path, output_path)
            logger.info(f"Copied to: {output_path}")
            logger.info(f"Cached as: {cached_path}")
        else:
            # No caching: build directly to output
            create_initramfs_archive(args.init_binary, output_path, modules, args.moddir)
            logger.info(f"Created initramfs: {output_path}")
    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(1)


def cmd_run(args):
    """Run QEMU with kernel and initramfs"""
    if not args.kernel.exists():
        logger.error(f"Kernel not found: {args.kernel}")
        sys.exit(1)

    if not args.initramfs.exists():
        logger.error(f"Initramfs not found: {args.initramfs}")
        sys.exit(1)

    # Create background task manager
    task_manager = BackgroundTaskManager()

    try:
        # Create virtiofs tasks (but don't start yet)
        if args.virtiofs:
            create_virtiofs_tasks(args.virtiofs, task_manager)

        # Start all background tasks
        task_manager.start_all()

        # Build QEMU command with optional DAX support for virtiofs
        enable_dax = args.virtiofs_dax and args.virtiofs
        qemu_cmd = QemuCommand(args.kernel, args.initramfs, args.memory, enable_dax)

        # Register all tasks with QEMU (adds runtime info like sockets)
        task_manager.register_all_with_qemu(qemu_cmd)

        # Set moddir for kernel module loading
        if args.moddir:
            qemu_cmd.init_config.moddir = args.moddir

        # Add additional cmdline
        if args.cmdline:
            qemu_cmd.add_cmdline(args.cmdline)

        # Build and run command
        cmd = qemu_cmd.build()
        logger.info("Running QEMU with command:")
        logger.info(" ".join(cmd))
        subprocess.run(cmd)
    except (ValueError, VirtiofsError) as e:
        logger.error(f"Error: {e}")
        sys.exit(1)
    finally:
        task_manager.cleanup()


def main():
    parser = argparse.ArgumentParser(prog="kdf", description="kdf: Kernel development flake tools")
    subparsers = parser.add_subparsers(dest="command", help="Subcommands")

    # build initramfs subcommand
    build_parser = subparsers.add_parser("build", help="Build subcommands")
    build_subparsers = build_parser.add_subparsers(dest="build_command")

    initramfs_parser = build_subparsers.add_parser("initramfs", help="Build initramfs cpio archive")
    initramfs_parser.add_argument("init_binary", type=Path, help="Path to init binary")
    initramfs_parser.add_argument("--output", "-o", type=Path, help="Output cpio file (default: cached)")
    initramfs_parser.add_argument("--module", "-m", action="append", dest="modules", help="Kernel module to include (can be specified multiple times)")
    initramfs_parser.add_argument("--moddir", default="/init-modules", help="Directory to store modules in initramfs (default: /init-modules)")
    initramfs_parser.add_argument("--no-cache", action="store_true", help="Skip cache check and don't cache result")

    # run subcommand
    run_parser = subparsers.add_parser("run", help="Run kernel with initramfs in QEMU")
    run_parser.add_argument("--kernel", type=Path, required=True, help="Path to kernel image")
    run_parser.add_argument("--initramfs", type=Path, required=True, help="Path to initramfs cpio")
    run_parser.add_argument("--virtiofs", "-v", action="append", help="Virtiofs share: tag:host_path:guest_path[:overlay]")
    run_parser.add_argument("--cmdline", default="", help="Additional kernel cmdline arguments")
    run_parser.add_argument("--memory", "-m", default="512M", help="QEMU memory (default: 512M)")
    run_parser.add_argument("--virtiofs-dax", action="store_true", help="Enable virtiofs DAX (shared memory backing) for better performance")
    run_parser.add_argument("--moddir", default="/init-modules", help="Directory to load kernel modules from (default: /init-modules)")

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
