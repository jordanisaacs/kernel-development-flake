"""Virtiofs daemon management for kdf."""

import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import IO, TYPE_CHECKING

from kdf_cli.bg_tasks import BackgroundTask, BackgroundTaskManager

if TYPE_CHECKING:
    from kdf_cli.qemu import QemuCommand

logger = logging.getLogger("kdf.virtiofs")


class VirtiofsError(Exception):
    """Base exception for virtiofs errors."""


class VirtiofsPathError(VirtiofsError):
    """Host path does not exist."""


class VirtiofsSocketError(VirtiofsError):
    """Socket creation failed."""


class Virtiofsd(BackgroundTask):
    """Manage a single virtiofsd daemon instance."""

    def __init__(
        self,
        tag: str,
        host_path: str,
        guest_path: str,
        with_overlay: bool,
        runtime_dir: Path,
        device_id: int,
        cache_mode: str = "auto",
    ) -> None:
        """Initialize virtiofsd configuration.

        Args:
            tag: Virtiofs tag name
            host_path: Host directory to share
            guest_path: Guest mount path
            with_overlay: Whether to use overlayfs
            runtime_dir: Directory for runtime files (sockets)
            device_id: Unique device ID for QEMU chardev
            cache_mode: Cache mode (none, auto, always). Default: auto

        """
        self.tag = tag
        self.host_path = Path(host_path)
        self.guest_path = guest_path
        self.with_overlay = with_overlay
        self.cache_mode = cache_mode
        self.socket_path = runtime_dir / f"{tag}.sock"
        self.device_id = device_id
        self.proc = None
        self.log_thread = None

    def start(self) -> None:
        """Start the virtiofsd daemon.

        Raises:
            VirtiofsPathError: If host path does not exist
            VirtiofsSocketError: If socket already exists or creation fails

        """
        if not self.host_path.exists():
            msg = f"Host path does not exist: {self.host_path}"
            raise VirtiofsPathError(msg)

        # Check for existing socket - fail if present (indicates running daemon)
        if self.socket_path.exists():
            msg = (
                f"Socket already exists for tag '{self.tag}': {self.socket_path}. "
                "Another virtiofsd may be running or socket was not cleaned up."
            )
            raise VirtiofsSocketError(
                msg,
            )

        # Build command
        # Cache mode: none=no caching (see all host changes immediately),
        #             auto=metadata caching (default),
        #             always=full caching (best performance)
        cmd = [
            "virtiofsd",
            "--socket-path",
            str(self.socket_path),
            "--shared-dir",
            str(self.host_path),
            "--sandbox",
            "none",
            "--cache",
            self.cache_mode,
        ]

        logger.info(
            "Starting virtiofsd for tag '%s' sharing %s", self.tag, self.host_path
        )
        logger.info("Command: %s", " ".join(cmd))

        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # Line buffered
        )

        # Start thread to read and log virtiofsd output
        def log_output(pipe: IO[str], prefix: str) -> None:
            for line in pipe:
                logger.info("[virtiofsd:%s] %s: %s", self.tag, prefix, line.rstrip())

        self.log_thread_stdout = threading.Thread(
            target=log_output,
            args=(self.proc.stdout, "stdout"),
            daemon=True,
        )
        self.log_thread_stderr = threading.Thread(
            target=log_output,
            args=(self.proc.stderr, "stderr"),
            daemon=True,
        )
        self.log_thread_stdout.start()
        self.log_thread_stderr.start()

        # Wait for socket to be created
        for _ in range(50):  # Wait up to 5 seconds
            if self.socket_path.exists():
                break
            time.sleep(0.1)
        else:
            self.stop()
            msg = f"Failed to create socket: {self.socket_path}"
            raise VirtiofsSocketError(msg)

    def stop(self) -> None:
        """Stop the virtiofsd daemon."""
        if self.proc and self.proc.poll() is None:
            logger.info("Stopping virtiofsd for tag '%s'", self.tag)
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "virtiofsd for tag '%s' did not terminate, killing",
                    self.tag,
                )
                self.proc.kill()
                self.proc.wait()

        # Cleanup socket file
        if self.socket_path.exists():
            self.socket_path.unlink()
            logger.info("Cleaned up socket: %s", self.socket_path)

    def register_with_qemu(self, qemu_cmd: "QemuCommand") -> None:
        """Register virtiofs device and kernel parameters with QEMU.

        Args:
            qemu_cmd: QemuCommand instance to configure

        """
        from kdf_cli.qemu import VirtiofsMount

        # Add virtiofs device to QEMU with virtiofs-specific chardev ID
        chardev_id = f"charvirtiofs{self.device_id}"
        qemu_cmd.add_qemu_args(
            "-chardev",
            f"socket,id={chardev_id},path={self.socket_path}",
            "-device",
            f"vhost-user-fs-pci,queue-size=1024,chardev={chardev_id},tag={self.tag}",
        )

        # Add to structured init config
        mount = VirtiofsMount(
            tag=self.tag,
            path=self.guest_path,
            with_overlay=self.with_overlay,
        )
        qemu_cmd.init_config.virtiofs_mounts.append(mount)


def create_virtiofs_tasks(
    virtiofs_specs: list[str],
    task_manager: BackgroundTaskManager,
) -> None:
    """Create virtiofs daemon tasks from specifications.

    Args:
        virtiofs_specs: List of virtiofs specs in format
            tag:host_path:guest_path[:overlay][:cache]
            where overlay is empty or "overlay", and cache is none/auto/always
            Examples: "share:/mnt:/mnt", "share:/mnt:/mnt:overlay",
                      "share:/mnt:/mnt::none", "share:/mnt:/mnt:overlay:always"
        task_manager: BackgroundTaskManager to register tasks with

    Raises:
        ValueError: If virtiofs spec format is invalid

    """
    if not virtiofs_specs:
        return

    runtime_dir = Path("/tmp/kdf-virtiofsd")
    runtime_dir.mkdir(exist_ok=True)

    for idx, share_spec in enumerate(virtiofs_specs):
        # Parse share_spec: tag:host_path:guest_path[:overlay][:cache]
        parts = share_spec.split(":")
        if len(parts) < 3:
            msg = (
                f"Invalid virtiofs spec '{share_spec}': "
                "must be tag:host_path:guest_path[:overlay][:cache]"
            )
            raise ValueError(
                msg,
            )

        tag = parts[0]
        host_path = parts[1]
        guest_path = parts[2]

        # Parse optional overlay (4th field - empty string or "overlay")
        with_overlay = False
        if len(parts) > 3:
            if parts[3] == "overlay":
                with_overlay = True
            elif parts[3] != "":
                msg = (
                    f"Invalid virtiofs spec '{share_spec}': "
                    f"fourth field must be empty or 'overlay', got '{parts[3]}'"
                )
                raise ValueError(msg)

        # Parse optional cache mode (5th field)
        cache_mode = "auto"
        if len(parts) > 4:
            if parts[4] in ("none", "auto", "always"):
                cache_mode = parts[4]
            else:
                msg = (
                    f"Invalid virtiofs spec '{share_spec}': "
                    f"fifth field must be cache mode (none/auto/always), "
                    f"got '{parts[4]}'"
                )
                raise ValueError(msg)

        # Create virtiofsd task
        vfsd = Virtiofsd(
            tag, host_path, guest_path, with_overlay, runtime_dir, idx, cache_mode
        )
        task_manager.add_task(vfsd)
