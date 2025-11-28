"""Nix integration for kernel resolution."""

import logging
import subprocess
import tempfile
from pathlib import Path

from kdf_cli.initramfs import create_initramfs_archive, get_prebuilt_init

logger = logging.getLogger("kdf.nix")

# Virtiofs module dependencies
# (order doesn't matter - dependency resolution happens during build)
VIRTIOFS_MODULES = [
    "drivers/virtio/virtio.ko",
    "drivers/virtio/virtio_ring.ko",
    "drivers/virtio/virtio_pci_modern_dev.ko",
    "drivers/virtio/virtio_pci_legacy_dev.ko",
    "drivers/virtio/virtio_pci.ko",
    "fs/fuse/fuse.ko",
    "fs/fuse/virtiofs.ko",
]


def get_system_kernel_version() -> str:
    """Get the current system kernel version using uname."""
    result = subprocess.run(["uname", "-r"], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def nix_build_output(nix_expr: str, output: str | None = None) -> str:
    """Build a Nix expression and return the output path.

    Args:
        nix_expr: Nix expression to build
        output: Optional output name (e.g., "modules", "dev").
            If None, uses default output.

    Returns:
        Nix store path as a string

    """
    full_expr = f"({nix_expr}).{output}" if output else nix_expr

    result = subprocess.run(
        ["nix-build", "--no-out-link", "-E", full_expr],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def get_kernel_derivations(version: str | None = None) -> tuple[str, str]:
    """Get the Nix store paths for kernel and modules derivations.

    Args:
        version: Kernel version string (e.g., "6.6" or "6.12")
            or None for default kernel

    Returns:
        Tuple of (kernel_drv_path, modules_drv_path)

    """
    if version is None or version == "":
        # Use default linuxPackages
        nix_expr = "with import <nixpkgs> {}; linuxPackages.kernel"
        logger.info("Using default linuxPackages kernel")
    else:
        # Parse version to get major.minor
        parts = version.split(".")
        if len(parts) < 2:
            msg = (
                f"Invalid kernel version format: {version} "
                "(need at least major.minor, e.g., '6.6')"
            )
            raise ValueError(
                msg,
            )

        major = parts[0]
        minor = parts[1]

        # Use linuxPackages_{major}_{minor}
        package_name = f"linuxPackages_{major}_{minor}"
        nix_expr = f"with import <nixpkgs> {{}}; {package_name}.kernel"
        logger.info("Using %s from nixpkgs", package_name)

    try:
        # Build kernel (default output)
        kernel_drv = nix_build_output(nix_expr)

        # Build modules output
        modules_drv = nix_build_output(nix_expr, "modules")

        logger.info("Kernel derivation: %s", kernel_drv)
        logger.info("Modules derivation: %s", modules_drv)

        return kernel_drv, modules_drv

    except subprocess.CalledProcessError as e:
        logger.exception("Failed to resolve kernel: %s", e.stderr)
        raise


def get_kernel_image_path(kernel_drv: str) -> Path:
    """Get the path to the kernel image (bzImage) from the kernel derivation.

    Args:
        kernel_drv: Nix store path to kernel derivation

    Returns:
        Path to kernel image

    """
    kernel_path = Path(kernel_drv)

    # Try common kernel image names
    for image_name in ["bzImage", "Image", "vmlinuz", "zImage"]:
        kernel_image = kernel_path / image_name
        if kernel_image.exists():
            logger.info("Found kernel image: %s", kernel_image)
            return kernel_image

    msg = f"Could not find kernel image in {kernel_path}"
    raise FileNotFoundError(msg)


def find_modules(modules_drv: str, module_patterns: list[str]) -> list[Path]:
    """Find kernel modules in the kernel modules directory.

    Args:
        modules_drv: Nix store path to kernel modules derivation
        module_patterns: List of module paths relative to lib/modules/VERSION/kernel/
                        (e.g., "drivers/virtio/virtio.ko")

    Returns:
        List of module paths (dependency resolution handled by initramfs builder)

    """
    modules = []
    modules_base = Path(modules_drv)

    # Find the kernel version directory
    modules_dir = modules_base / "lib" / "modules"
    kernel_dirs = list(modules_dir.glob("*"))
    if not kernel_dirs:
        msg = f"No kernel version directories found in {modules_dir}"
        raise FileNotFoundError(msg)

    kernel_dir = kernel_dirs[0]
    kernel_base = kernel_dir / "kernel"

    for pattern in module_patterns:
        # Try with compression extensions
        found = False
        for ext in [".xz", ".gz", ""]:
            module_path = Path(str(kernel_base / pattern) + ext)
            if module_path.exists():
                modules.append(module_path)
                logger.info("Found module: %s", module_path)
                found = True
                break

        if not found:
            msg = f"Could not find module {pattern} in {kernel_base}"
            raise FileNotFoundError(msg)

    return modules


def resolve_nix_packages(package_attrs: list[str]) -> str:
    """Resolve Nix package attributes and generate a PATH string using makeBinPath.

    Args:
        package_attrs: List of package attribute names (e.g., ["busybox", "python3"])

    Returns:
        A colon-separated PATH string containing bin directories from all packages

    """
    if not package_attrs:
        return ""

    # Build Nix expression that creates a list of packages and uses makeBinPath
    packages_list = " ".join(package_attrs)
    nix_expr = f"with import <nixpkgs> {{}}; lib.makeBinPath [ {packages_list} ]"

    try:
        result = subprocess.run(
            ["nix", "eval", "--raw", "--impure", "--expr", nix_expr],
            capture_output=True,
            text=True,
            check=True,
        )
        # nix eval --raw returns the raw string without quotes
        bin_path = result.stdout.strip()
        logger.info("Resolved packages %s to PATH: %s", package_attrs, bin_path)
        return bin_path
    except subprocess.CalledProcessError as e:
        logger.exception("Failed to resolve packages %s: %s", package_attrs, e.stderr)
        raise


def resolve_kernel_and_initramfs(
    version: str | None = None,
    custom_initramfs: Path | None = None,
) -> tuple[Path, Path]:
    """High-level function to resolve kernel and initramfs from nixpkgs.

    If custom_initramfs is provided, it will be used as-is.
    Otherwise, builds an initramfs with virtiofs modules from the resolved kernel.

    Args:
        version: Kernel version string or None to use system kernel
        custom_initramfs: Optional custom initramfs path.
            If None, builds one with virtiofs modules.

    Returns:
        Tuple of (kernel_image_path, initramfs_path)

    """
    # Get kernel and modules derivations
    kernel_drv, modules_drv = get_kernel_derivations(version)

    # Get kernel image path
    kernel_image = get_kernel_image_path(kernel_drv)

    # If custom initramfs provided, use it
    if custom_initramfs is not None:
        return kernel_image, custom_initramfs

    # Otherwise, build initramfs with virtiofs modules
    # Get prebuilt init binary
    init_binary = get_prebuilt_init()
    if init_binary is None:
        msg = (
            "No prebuilt init binary available. "
            "Please build kdf-cli from the Nix package or provide --initramfs."
        )
        raise FileNotFoundError(
            msg,
        )

    # Find virtiofs modules
    modules = find_modules(modules_drv, VIRTIOFS_MODULES)

    # Create temporary initramfs file
    import os

    fd, initramfs_tmpfile = tempfile.mkstemp(suffix=".cpio", prefix="kdf-initramfs-")
    os.close(fd)  # Close the file descriptor, we just need the path
    initramfs_path = Path(initramfs_tmpfile)

    logger.info("Building initramfs with %d virtiofs modules", len(modules))
    create_initramfs_archive(init_binary, initramfs_path, modules, "/init-modules")

    return kernel_image, initramfs_path
