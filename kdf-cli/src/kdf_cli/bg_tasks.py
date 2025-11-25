"""Background task management for kdf"""

import atexit
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qemu import QemuCommand


class BackgroundTask(ABC):
    """Abstract base class for background tasks"""

    @abstractmethod
    def start(self):
        """Start the background task"""
        pass

    @abstractmethod
    def stop(self):
        """Stop the background task"""
        pass

    def register_with_qemu(self, qemu_cmd: "QemuCommand"):
        """Register this task's QEMU configuration

        Override this method if the task needs to add QEMU arguments
        or kernel cmdline parameters after starting.

        Args:
            qemu_cmd: QemuCommand instance to configure
        """
        pass


class BackgroundTaskManager:
    """Manage all background processes/tasks"""

    def __init__(self):
        self.tasks = []
        atexit.register(self.cleanup)

    def add_task(self, task: BackgroundTask):
        """Add a background task to be managed

        Args:
            task: BackgroundTask instance
        """
        self.tasks.append(task)

    def start_all(self):
        """Start all registered background tasks"""
        for task in self.tasks:
            task.start()

    def register_all_with_qemu(self, qemu_cmd: "QemuCommand"):
        """Register all tasks with QEMU command

        Args:
            qemu_cmd: QemuCommand instance to configure
        """
        for task in self.tasks:
            task.register_with_qemu(qemu_cmd)

    def cleanup(self):
        """Cleanup all background tasks"""
        for task in self.tasks:
            task.stop()
