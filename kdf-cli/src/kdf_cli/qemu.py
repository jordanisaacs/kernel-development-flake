"""QEMU command building and management for kdf"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict


@dataclass
class VirtiofsMount:
    """Virtiofs mount specification (matches kdf-init)"""
    tag: str
    path: str
    with_overlay: bool


@dataclass
class Symlink:
    """Symlink specification (matches kdf-init)"""
    source: str
    target: str


@dataclass
class InitConfig:
    """Configuration for kdf-init (matches kdf-init/src/cmdline.rs)"""
    virtiofs_mounts: List[VirtiofsMount] = field(default_factory=list)
    symlinks: List[Symlink] = field(default_factory=list)
    env_vars: Dict[str, str] = field(default_factory=dict)
    command: Optional[str] = None
    moddir: Optional[str] = None

    def to_cmdline(self) -> List[str]:
        """Convert init configuration to kernel cmdline parameters

        Returns:
            List of init.XXX kernel parameters
        """
        params = []

        # Build init.virtiofs parameter
        if self.virtiofs_mounts:
            specs = []
            for mount in self.virtiofs_mounts:
                if mount.with_overlay:
                    specs.append(f"{mount.tag}:{mount.path}:Y")
                else:
                    specs.append(f"{mount.tag}:{mount.path}")
            params.append(f"init.virtiofs={','.join(specs)}")

        # Build init.symlinks parameter
        if self.symlinks:
            specs = [f"{sym.source}:{sym.target}" for sym in self.symlinks]
            params.append(f"init.symlinks={','.join(specs)}")

        # Build init.env.XXX parameters
        for key, value in self.env_vars.items():
            params.append(f"init.env.{key}={value}")

        # Build init.cmd parameter
        if self.command:
            params.append(f"init.cmd={self.command}")

        # Build init.moddir parameter
        if self.moddir:
            params.append(f"init.moddir={self.moddir}")

        return params


class QemuCommand:
    """Builder for QEMU command arguments"""

    def __init__(self, kernel: Path, initramfs: Path, memory: str = "512M", enable_dax: bool = False):
        """Initialize QEMU command builder

        Args:
            kernel: Path to kernel image
            initramfs: Path to initramfs cpio
            memory: QEMU memory (default: 512M)
            enable_dax: Enable DAX (Direct Access) for virtiofs by backing all RAM with shared memory
        """
        self.kernel = kernel
        self.initramfs = initramfs
        self.memory = memory
        self.enable_dax = enable_dax
        self.qemu_args = []
        self.cmdline_parts = ["console=ttyS0"]
        self.init_config = InitConfig()

    def add_qemu_args(self, *args: str):
        """Add QEMU command-line arguments

        Args:
            *args: Variable number of QEMU arguments
        """
        self.qemu_args.extend(args)

    def add_cmdline(self, param: str):
        """Add kernel command-line parameter

        Args:
            param: Kernel cmdline parameter
        """
        self.cmdline_parts.append(param)

    def build(self) -> List[str]:
        """Build final QEMU command

        Returns:
            List of QEMU command arguments
        """
        cmd = [
            "qemu-system-x86_64",
            "-kernel", str(self.kernel),
            "-initrd", str(self.initramfs),
            "-nographic",
            "-serial", "mon:stdio",
        ]

        # Add memory configuration
        # When DAX is enabled for virtiofs, all guest RAM is backed by shared memory
        # using memory-backend-memfd for zero-copy file access
        if self.enable_dax:
            cmd.extend([
                "-m", self.memory,
                "-object", f"memory-backend-memfd,id=mem,size={self.memory},share=on",
                "-numa", "node,memdev=mem",
            ])
        else:
            cmd.extend(["-m", self.memory])

        # Add any additional QEMU args
        cmd.extend(self.qemu_args)

        # Build complete kernel cmdline (base + init config)
        cmdline = self.cmdline_parts + self.init_config.to_cmdline()
        cmd.extend(["-append", " ".join(cmdline)])

        return cmd
