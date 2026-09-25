"""
Event loop multiplexer using select.poll with grace-period scheduling.
"""

import select
import sys
import threading
import time
from typing import Dict, Optional

from .config import DaemonConfig
from .inotify import Inotify
from .sorter import is_valid_file, move_file

class Watcher:
    """Manages the inotify polling loop and grace-period cooldowns."""

    def __init__(self, config: DaemonConfig):
        self.config = config
        self.pending_files: Dict[str, float] = {}

    def schedule_file(self, filename: str, delay_seconds: Optional[float] = None) -> None:
        """Add or update a file in the grace period queue."""
        if not is_valid_file(filename, self.config):
            return

        filepath = self.config.downloads_dir / filename
        if not filepath.is_file():
            return

        delay = self.config.grace_period_seconds if delay_seconds is None else max(0.0, delay_seconds)
        due_time = time.time() + delay
        self.pending_files[filename] = due_time

        mins = round(delay / 60, 1)
        if delay > 0:
            print(f"[SCHEDULED] '{filename}' available in Downloads. Will organize in {mins} min.")
        else:
            print(f"[SCHEDULED] '{filename}' ready for immediate organization.")
        sys.stdout.flush()

    def process_existing_files(self) -> None:
        """Scan Downloads directory upon startup."""
        now = time.time()
        try:
            for item in self.config.downloads_dir.iterdir():
                if item.is_file() and is_valid_file(item.name, self.config):
                    mtime = item.stat().st_mtime
                    age = now - mtime
                    if age >= self.config.grace_period_seconds:
                        move_file(item.name, self.config)
                    else:
                        remaining = self.config.grace_period_seconds - age
                        self.schedule_file(item.name, delay_seconds=remaining)
        except Exception as e:
            print(f"[WARNING] Initial scan error: {e}", file=sys.stderr)

    def run(self, stop_event: Optional[threading.Event] = None) -> None:
        """Start the inotify select.poll event loop."""
        self.config.downloads_dir.mkdir(parents=True, exist_ok=True)
        if self.config.is_destination_available():
            self.config.destination_dir.mkdir(parents=True, exist_ok=True)
        else:
            print(
                f"[WARNING] Destination {self.config.destination_dir} is not currently available/mounted. Files will remain queued in Downloads.",
                file=sys.stderr,
            )
            sys.stderr.flush()

        inotify_service = Inotify(self.config.downloads_dir)
        poller = select.poll()
        poller.register(inotify_service.fd, select.POLLIN)

        grace_min = int(self.config.grace_period_seconds / 60)
        print(f"[DAEMON] Monitoring {self.config.downloads_dir} with a {grace_min}-minute grace period...")
        sys.stdout.flush()

        self.process_existing_files()

        try:
            while stop_event is None or not stop_event.is_set():
                now = time.time()

                # Dynamic sleep: poll sleeps until the next pending file expires or up to 5s
                if self.pending_files:
                    next_due = min(self.pending_files.values())
                    wait_ms = max(0, min(5000, int((next_due - now) * 1000)))
                else:
                    wait_ms = 5000

                events = poller.poll(wait_ms)

                # 1. Handle inotify events
                for poll_fd, event in events:
                    if poll_fd == inotify_service.fd and (event & select.POLLIN):
                        for name in inotify_service.read_events():
                            self.schedule_file(name)

                # 2. Process expired files
                now = time.time()
                expired = [fname for fname, due in list(self.pending_files.items()) if due <= now]
                for fname in expired:
                    if not self.config.is_destination_available():
                        self.pending_files[fname] = now + 30
                        print(
                            f"[WARNING] Destination {self.config.destination_dir} unavailable. Retrying '{fname}' in 30s.",
                            file=sys.stderr,
                        )
                        sys.stderr.flush()
                        continue
                    self.pending_files.pop(fname, None)
                    move_file(fname, self.config)

        finally:
            inotify_service.close()
