#!/usr/bin/env python3
"""
Runtime module debugging with GDB
This script connects to a QEMU VM and loads kernel module symbols dynamically
"""

import argparse
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Connect to QEMU VM with GDB and load kernel module symbols"
    )
    parser.add_argument(
        "--module-dirs",
        nargs="*",
        default=[],
        help="Directories containing kernel modules (source & symbols)",
    )
    parser.add_argument(
        "--vmlinux-dir",
        required=True,
        help="Directory containing vmlinux file",
    )
    parser.add_argument(
        "--kernel-version",
        required=True,
        help="Kernel version string",
    )
    parser.add_argument(
        "--port",
        default="1234",
        help="GDB remote port (default: 1234)",
    )

    args = parser.parse_args()

    # Build paths from vmlinux directory
    vmlinux_dir = Path(args.vmlinux_dir)
    vmlinux_path = vmlinux_dir / "vmlinux"
    source_dir = vmlinux_dir / "lib/modules" / args.kernel_version / "source"
    build_dir = vmlinux_dir / "lib/modules" / args.kernel_version / "build"
    vmlinux_gdb = build_dir / "vmlinux-gdb.py"

    # Validate paths
    if not vmlinux_path.exists():
        print(f"Error: vmlinux not found at {vmlinux_path}", file=sys.stderr)
        sys.exit(1)

    if not source_dir.exists():
        print(f"Error: source directory not found at {source_dir}", file=sys.stderr)
        sys.exit(1)

    if not build_dir.exists():
        print(f"Error: build directory not found at {build_dir}", file=sys.stderr)
        sys.exit(1)

    # Build GDB command
    gdb_args = ["gdb"]

    # Add source directories for kernel
    gdb_args.extend(["-ex", f"dir {source_dir}/scripts"])

    # Add source directories for modules
    for module_dir in args.module_dirs:
        module_path = Path(module_dir)
        if module_path.exists():
            print(f"Adding module source directory: {module_dir}")
            gdb_args.extend(["-ex", f"dir {module_dir}"])
        else:
            print(f"Warning: Module directory '{module_dir}' does not exist", file=sys.stderr)

    # Load vmlinux
    gdb_args.extend(["-ex", f"file {vmlinux_path}"])

    # Source kernel GDB scripts if available
    if vmlinux_gdb.exists():
        gdb_args.extend(["-ex", f"source {vmlinux_gdb}"])
    else:
        print(f"Warning: vmlinux-gdb.py not found at {vmlinux_gdb}", file=sys.stderr)

    # Create symbol loading alias if module directories provided
    if args.module_dirs:
        symbols_dirs = " ".join(args.module_dirs)
        gdb_args.extend(["-ex", f"alias lx-symbols-runtime = lx-symbols {symbols_dirs}"])
        print("Use 'lx-symbols-runtime' in GDB to load module symbols from runtime directories")

    # Connect to remote target
    gdb_args.extend(["-ex", f"target remote localhost:{args.port}"])

    # Launch GDB
    os.execvp("gdb", gdb_args)


if __name__ == "__main__":
    main()
