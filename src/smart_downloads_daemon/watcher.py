"""
Event loop multiplexer using select.poll with grace-period scheduling.
"""

import json
import os
import select
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import DaemonConfig
from .inotify import Inotify
from .sorter import is_valid_file, move_file


class Watcher:
    """Manages the inotify polling loop, pause states, and grace-period cooldowns."""

    def __init__(self, config: DaemonConfig, state_path: Optional[Path] = None):
        self.config = config
        self.state_path = state_path or config.state_file
        self.pending_files: Dict[str, float] = {}
        self.recent_moves: List[Dict[str, Any]] = []
        self._is_paused: bool = False
        self._destination_unavailable_logged: bool = False
        self.start_time: float = time.time()

    def save_state(self) -> None:
        """Save runtime status to state.json for CLI status inspection."""
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.state_path.with_suffix(".tmp")
            now = time.time()
            data = {
                "pid": os.getpid(),
                "started_at": self.start_time,
                "last_updated": now,
                "paused": self.config.is_paused(),
                "destination_available": self.config.is_destination_available(),
                "destination_dir": str(self.config.destination_dir),
                "downloads_dir": str(self.config.downloads_dir),
                "pending_count": len(self.pending_files),
                "pending_files": {
                    fname: {
                        "due_time": due,
                        "seconds_remaining": max(0.0, round(due - now, 1)),
                    }
                    for fname, due in sorted(self.pending_files.items(), key=lambda x: x[1])
                },
                "recent_moves": self.recent_moves[-10:],
            }
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            tmp_path.replace(self.state_path)
        except Exception:
            pass

    def cleanup_state(self) -> None:
        """Remove state file upon clean shutdown."""
        try:
            if self.state_path.is_file():
                self.state_path.unlink()
        except OSError:
            pass

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
        self.save_state()

    def process_existing_files(self) -> None:
        """Scan Downloads directory upon startup."""
        now = time.time()
        try:
            for item in self.config.downloads_dir.iterdir():
                if item.is_file() and is_valid_file(item.name, self.config):
                    mtime = item.stat().st_mtime
                    age = now - mtime
                    if age >= self.config.grace_period_seconds:
                        if self.config.is_paused():
                            self.schedule_file(item.name, delay_seconds=0)
                        elif move_file(item.name, self.config) is None and item.is_file():
                            self.schedule_file(item.name, delay_seconds=30)
                    else:
                        remaining = self.config.grace_period_seconds - age
                        self.schedule_file(item.name, delay_seconds=remaining)
        except Exception as e:
            print(f"[WARNING] Initial scan error: {e}", file=sys.stderr)
        self.save_state()

    def run(self, stop_event: Optional[threading.Event] = None) -> None:
        """Start the inotify select.poll event loop."""
        self.config.downloads_dir.mkdir(parents=True, exist_ok=True)
        if not self.config.is_destination_available():
            self.config.try_automount_destination()

        if self.config.is_destination_available():
            self.config.destination_dir.mkdir(parents=True, exist_ok=True)
        else:
            print(
                f"[WARNING] Destination {self.config.destination_dir} is not currently available/mounted. Files will remain queued in Downloads.",
                file=sys.stderr,
            )
            sys.stderr.flush()
            self._destination_unavailable_logged = True

        inotify_service = Inotify(self.config.downloads_dir)
        poller = select.poll()
        poller.register(inotify_service.fd, select.POLLIN)

        grace_min = int(self.config.grace_period_seconds / 60)
        print(f"[DAEMON] Monitoring {self.config.downloads_dir} with a {grace_min}-minute grace period...")
        sys.stdout.flush()

        self._is_paused = self.config.is_paused()
        if self._is_paused:
            print("[PAUSED] Daemon started in PAUSED state. Automated file sorting is suspended.", file=sys.stdout)
            sys.stdout.flush()

        self.process_existing_files()
        self.save_state()

        try:
            while stop_event is None or not stop_event.is_set():
                now = time.time()

                # 1. Handle pause state transitions
                is_paused = self.config.is_paused()
                if is_paused != self._is_paused:
                    self._is_paused = is_paused
                    if is_paused:
                        print("[PAUSED] Daemon is paused by user. Automated file sorting is suspended.", file=sys.stdout)
                    else:
                        print("[RESUMED] Daemon resumed by user. Automated file sorting is active.", file=sys.stdout)
                    sys.stdout.flush()
                    self.save_state()

                # Dynamic sleep: poll sleeps until the next pending file expires or up to 5s
                if not self._is_paused and self.pending_files:
                    next_due = min(self.pending_files.values())
                    wait_ms = max(0, min(5000, int((next_due - now) * 1000)))
                else:
                    wait_ms = 5000

                events = poller.poll(wait_ms)

                # 2. Handle inotify events
                for poll_fd, event in events:
                    if poll_fd == inotify_service.fd and (event & select.POLLIN):
                        for name in inotify_service.read_events():
                            self.schedule_file(name)

                # If paused, hold files without moving them
                if self._is_paused:
                    continue

                # 3. Process expired files
                now = time.time()
                expired = [fname for fname, due in list(self.pending_files.items()) if due <= now]
                if not expired:
                    continue

                # Verify destination availability
                if not self.config.is_destination_available():
                    self.config.try_automount_destination()

                if not self.config.is_destination_available():
                    if not self._destination_unavailable_logged:
                        print(
                            f"[WARNING] Destination {self.config.destination_dir} unavailable. "
                            f"Holding {len(expired)} file(s) safely in Downloads.",
                            file=sys.stderr,
                        )
                        sys.stderr.flush()
                        self._destination_unavailable_logged = True

                    for fname in expired:
                        self.pending_files[fname] = now + 30
                    self.save_state()
                    continue

                # Destination recovered
                if self._destination_unavailable_logged:
                    print(
                        f"[OK] Destination {self.config.destination_dir} is now available. Resuming organization.",
                        file=sys.stdout,
                    )
                    sys.stdout.flush()
                    self._destination_unavailable_logged = False

                for fname in expired:
                    moved = move_file(fname, self.config)
                    if moved is None and (self.config.downloads_dir / fname).is_file():
                        self.pending_files[fname] = time.time() + 30
                    else:
                        self.pending_files.pop(fname, None)
                        if moved is not None:
                            self.recent_moves.append({
                                "file": fname,
                                "destination": str(moved),
                                "time": time.time(),
                                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            })
                            if len(self.recent_moves) > 10:
                                self.recent_moves.pop(0)

                self.save_state()

        finally:
            self.cleanup_state()
            inotify_service.close()
