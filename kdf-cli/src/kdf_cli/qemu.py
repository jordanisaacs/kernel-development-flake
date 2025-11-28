"""QEMU command building and management for kdf."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class VirtiofsMount:
    """Virtiofs mount specification (matches kdf-init)."""

    tag: str
    path: str
    with_overlay: bool


@dataclass
class Symlink:
    """Symlink specification (matches kdf-init)."""

    source: str
    target: str


@dataclass
class InitConfig:
    """Configuration for kdf-init (matches kdf-init/src/cmdline.rs)."""

    virtiofs_mounts: list[VirtiofsMount] = field(default_factory=list)
    symlinks: list[Symlink] = field(default_factory=list)
    env_vars: dict[str, str] = field(default_factory=dict)
    shell: str | None = None
    script: str | None = None
    moddir: str | None = None
    console: str = "console"
    chdir: str | None = None

    def to_cmdline(self) -> list[str]:
        """Convert init configuration to kernel cmdline parameters.

        Returns:
            List of kernel cmdline parameters (console= and init.XXX)

        Raises:
            ValueError: If both shell and command are set, or neither is set

        """
        params = []

        # Validate that shell is always set (script is optional)
        if self.shell is None:
            msg = "Shell is required"
            raise ValueError(msg)

        # Build console= kernel parameter (for kernel output)
        params.append(f"console={self.console}")

        # Build init.console parameter (required, for init to open the console device)
        # Note: Pass just the device name; init will prepend /dev/ when opening
        params.append(f"init.console={self.console}")

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

        # Build init.shell parameter (required)
        # Wrap in backticks to preserve spaces
        params.append(f"init.shell=`{self.shell}`")

        # Build init.script parameter (optional)
        if self.script is not None:
            # Wrap in backticks to preserve spaces
            params.append(f"init.script=`{self.script}`")

        # Build init.moddir parameter
        if self.moddir:
            params.append(f"init.moddir={self.moddir}")

        # Build init.chdir parameter
        if self.chdir:
            params.append(f"init.chdir={self.chdir}")

        return params


class QemuCommand:
    """Builder for QEMU command arguments."""

    def __init__(
        self,
        kernel: Path,
        initramfs: Path,
        memory: str = "512M",
        debug: bool = False,
    ) -> None:
        """Initialize QEMU command builder.

        Args:
            kernel: Path to kernel image
            initramfs: Path to initramfs cpio
            memory: QEMU memory (default: 512M)
            debug: Enable GDB debugging on port 1234 (default: False)

        """
        self.kernel = kernel
        self.initramfs = initramfs
        self.memory = memory
        self.debug = debug
        # Always use shared memory backing (required for vhost-user devices like
        # virtiofs)
        self.qemu_args = [
            "-m",
            memory,
            "-object",
            f"memory-backend-memfd,id=mem,size={memory},share=on",
            "-numa",
            "node,memdev=mem",
        ]
        self.cmdline_parts = []
        # Console device name without /dev/ prefix to match init.console parameter
        self.init_config = InitConfig(
            console="ttyS0",
        )

    def add_qemu_args(self, *args: str) -> None:
        """Add QEMU command-line arguments.

        Args:
            *args: Variable number of QEMU arguments

        """
        self.qemu_args.extend(args)

    def add_cmdline(self, param: str) -> None:
        """Add kernel command-line parameter.

        Args:
            param: Kernel cmdline parameter

        """
        self.cmdline_parts.append(param)

    def build(self) -> list[str]:
        """Build final QEMU command.

        Returns:
            List of QEMU command arguments

        """
        cmd = [
            "qemu-system-x86_64",
            "-machine",
            "accel=kvm:tcg",
            "-cpu",
            "host",
            "-kernel",
            str(self.kernel),
            "-initrd",
            str(self.initramfs),
            "-nographic",
            "-serial",
            "mon:stdio",
        ]

        # Add debug support if enabled (GDB server on port 1234)
        if self.debug:
            cmd.append("-s")

        # Add configured QEMU args (includes memory configuration)
        cmd.extend(self.qemu_args)

        # Build complete kernel cmdline (base + init config)
        cmdline = self.cmdline_parts + self.init_config.to_cmdline()
        cmd.extend(["-append", " ".join(cmdline)])

        return cmd
