"""
Event loop multiplexer using select.poll with grace-period scheduling.
"""

import json
import os
import queue
import select
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import DaemonConfig
from .inotify import (
    IN_CLOSE_NOWRITE, IN_CLOSE_WRITE, IN_CREATE, IN_DELETE, IN_DELETE_SELF,
    IN_IGNORED, IN_MOVE_SELF, IN_MOVED_FROM, IN_MOVED_TO, IN_OPEN,
    IN_Q_OVERFLOW, IN_UNMOUNT, Inotify,
)
from .sorter import is_folder_in_flight, is_valid_item, move_file


class Watcher:
    """Manages the inotify polling loop, pause states, and grace-period cooldowns."""

    def __init__(self, config: DaemonConfig, state_path: Optional[Path] = None):
        self.config = config
        self.state_path = state_path or config.state_file
        self.pending_files: Dict[str, float] = {}
        self._signatures: Dict[str, tuple] = {}
        self._open_counts: Dict[str, int] = {}
        self._moving_name: Optional[str] = None
        self._move_thread: Optional[threading.Thread] = None
        self._move_results = queue.Queue()
        self.recent_moves: List[Dict[str, Any]] = []
        self._is_paused: bool = False
        self._destination_unavailable_logged: bool = False
        self._dest_avail_cache: Optional[bool] = None
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
                "destination_available": self._dest_avail_cache,
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

    def schedule_file(
        self,
        filename: str,
        delay_seconds: Optional[float] = None,
        notify: bool = True,
    ) -> None:
        """Add or update a file or directory in the grace period queue."""
        filepath = self.config.downloads_dir / filename
        if filepath.is_symlink():
            if self.pending_files.pop(filename, None) is not None:
                self._signatures.pop(filename, None)
                self.save_state()
            return
        if not (filepath.is_file() or filepath.is_dir()):
            if filename in self.pending_files:
                self.pending_files.pop(filename, None)
                self._signatures.pop(filename, None)
                self._open_counts.pop(filename, None)
                self.save_state()
            return

        # A nested incomplete download has no completion event on the root
        # watch. Keep its parent directory in the retry queue.
        if filepath.is_dir() and is_folder_in_flight(filepath, self.config):
            self.pending_files[filename] = time.time() + 30
            return
        if not is_valid_item(filename, self.config, base_dir=self.config.downloads_dir):
            self.pending_files.pop(filename, None)
            self._signatures.pop(filename, None)
            return

        delay = self.config.grace_period_seconds if delay_seconds is None else max(0.0, delay_seconds)
        due_time = time.time() + delay

        is_already_scheduled = filename in self.pending_files
        self.pending_files[filename] = due_time
        try:
            stat = filepath.stat()
            self._signatures[filename] = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
        except OSError:
            pass

        item_type = "directory" if filepath.is_dir() else "file"
        mins = round(delay / 60, 1)

        # Only print, save state, and dispatch notification if newly scheduled or explicit delay given
        # This prevents duplicate log storms and excessive disk churn on multiple inotify events for same file
        if not is_already_scheduled or delay_seconds is not None:
            if delay > 0:
                print(f"[SCHEDULED] '{filename}' ({item_type}) available in Downloads. Will organize in {mins} min.")
            else:
                print(f"[SCHEDULED] '{filename}' ({item_type}) ready for immediate organization.")
            sys.stdout.flush()
            self.save_state()

            if not is_already_scheduled and notify and delay > 0 and "PYTEST_CURRENT_TEST" not in os.environ:
                try:
                    from smart_downloads_daemon.notifier import notify_scheduled
                    notify_scheduled(
                        source_name=filename,
                        delay_minutes=mins,
                        is_dir=filepath.is_dir(),
                        config=self.config,
                    )
                except Exception:
                    pass

    def process_existing_files(self, defer_moves: bool = False) -> None:
        """Scan Downloads directory upon startup."""
        now = time.time()
        try:
            for item in self.config.downloads_dir.iterdir():
                if (item.is_file() or item.is_dir()) and (
                    is_valid_item(item.name, self.config, base_dir=self.config.downloads_dir)
                    or (item.is_dir() and not item.is_symlink() and is_folder_in_flight(item, self.config))
                ):
                    if item.is_dir() and is_folder_in_flight(item, self.config):
                        self.schedule_file(item.name, delay_seconds=30, notify=False)
                        continue
                    if item.is_dir():
                        try:
                            all_mtimes = [item.stat().st_mtime] + [f.stat().st_mtime for f in item.rglob("*")]
                            mtime = max(all_mtimes)
                        except Exception:
                            mtime = item.stat().st_mtime
                    else:
                        mtime = item.stat().st_mtime

                    age = now - mtime
                    if age >= self.config.grace_period_seconds:
                        if self.config.is_paused():
                            self.schedule_file(item.name, delay_seconds=0, notify=False)
                        elif defer_moves:
                            self.schedule_file(item.name, delay_seconds=0, notify=False)
                        elif move_file(item.name, self.config) is None and (item.is_file() or item.is_dir()):
                            self.schedule_file(item.name, delay_seconds=30, notify=False)
                    else:
                        remaining = self.config.grace_period_seconds - age
                        self.schedule_file(item.name, delay_seconds=remaining, notify=False)
        except Exception as e:
            print(f"[WARNING] Initial scan error: {e}", file=sys.stderr)
        self.save_state()

    def _start_move(self, filename: str, wake_fd: int) -> None:
        self._moving_name = filename

        def work() -> None:
            try:
                if not self.config.is_destination_available():
                    self.config.try_automount_destination()
                available = self.config.is_destination_available()
                moved = move_file(filename, self.config)
            except Exception as error:
                print(f"[ERROR] Failed to move {filename}: {error}", file=sys.stderr)
                moved = None
                available = False
            self._move_results.put((filename, moved, available))
            try:
                os.write(wake_fd, b"x")
            except OSError:
                pass

        self._move_thread = threading.Thread(target=work, daemon=True, name="smartdown-transfer")
        self._move_thread.start()

    def _collect_moves(self) -> None:
        while True:
            try:
                fname, moved, available = self._move_results.get_nowait()
            except queue.Empty:
                break
            self._moving_name = None
            self._dest_avail_cache = available
            if available and self._destination_unavailable_logged:
                print(f"[OK] Destination {self.config.destination_dir} is now available.")
                self._destination_unavailable_logged = False
            elif not available and not self._destination_unavailable_logged:
                print(f"[WARNING] Destination {self.config.destination_dir} is unavailable; retaining files in Downloads.", file=sys.stderr)
                self._destination_unavailable_logged = True
            if moved is None and (self.config.downloads_dir / fname).exists():
                self.pending_files[fname] = time.time() + 30
            else:
                self.pending_files.pop(fname, None)
                self._signatures.pop(fname, None)
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

    def run(self, stop_event: Optional[threading.Event] = None) -> None:
        """Start the inotify select.poll event loop."""
        self.config.downloads_dir.mkdir(parents=True, exist_ok=True)

        inotify_service = Inotify(self.config.downloads_dir)
        poller = select.poll()
        poller.register(inotify_service.fd, select.POLLIN | select.POLLERR | select.POLLHUP)
        wake_read, wake_write = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        poller.register(wake_read, select.POLLIN)

        grace_min = int(self.config.grace_period_seconds / 60)
        print(f"[DAEMON] Monitoring {self.config.downloads_dir} with a {grace_min}-minute grace period...")
        sys.stdout.flush()

        self._is_paused = self.config.is_paused()
        if self._is_paused:
            print("[PAUSED] Daemon started in PAUSED state. Automated file sorting is suspended.", file=sys.stdout)
            sys.stdout.flush()

        self.process_existing_files(defer_moves=True)
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
                waiting = [due for name, due in self.pending_files.items() if name != self._moving_name]
                if self._moving_name is not None:
                    wait_ms = 5000
                elif not self._is_paused and waiting:
                    next_due = min(waiting)
                    wait_ms = max(0, min(5000, int((next_due - now) * 1000)))
                else:
                    wait_ms = 5000

                events = poller.poll(wait_ms)

                # 2. Handle inotify events
                rebuild_watch = False
                reconcile = False
                for poll_fd, poll_mask in events:
                    if poll_fd == wake_read:
                        try:
                            os.read(wake_read, 4096)
                        except BlockingIOError:
                            pass
                        continue
                    if poll_fd != inotify_service.fd:
                        continue
                    if poll_mask & (select.POLLERR | select.POLLHUP | select.POLLNVAL):
                        rebuild_watch = True
                        break
                    if poll_mask & select.POLLIN:
                        try:
                            for event in inotify_service.read_events():
                                if event.mask & IN_Q_OVERFLOW:
                                    reconcile = True
                                elif event.mask & (IN_IGNORED | IN_UNMOUNT | IN_DELETE_SELF | IN_MOVE_SELF):
                                    rebuild_watch = True
                                elif event.name:
                                    if event.mask & IN_OPEN:
                                        self._open_counts[event.name] = self._open_counts.get(event.name, 0) + 1
                                    if event.mask & (IN_CLOSE_WRITE | IN_CLOSE_NOWRITE):
                                        count = self._open_counts.get(event.name, 0)
                                        if count <= 1:
                                            self._open_counts.pop(event.name, None)
                                        else:
                                            self._open_counts[event.name] = count - 1
                                    if event.mask & (IN_CREATE | IN_MOVED_TO | IN_CLOSE_WRITE | IN_DELETE | IN_MOVED_FROM):
                                        self.schedule_file(event.name)
                        except OSError:
                            rebuild_watch = True

                if rebuild_watch:
                    poller.unregister(inotify_service.fd)
                    inotify_service.close()
                    inotify_service = Inotify(self.config.downloads_dir)
                    poller.register(inotify_service.fd, select.POLLIN | select.POLLERR | select.POLLHUP)
                    self._open_counts.clear()
                    reconcile = True
                if reconcile:
                    self.process_existing_files(defer_moves=True)

                self._collect_moves()

                # If paused, hold files without moving them
                if self._is_paused:
                    continue

                # 3. Process expired files
                now = time.time()
                expired = [fname for fname, due in list(self.pending_files.items())
                           if due <= now and fname != self._moving_name]
                if not expired:
                    continue

                for fname in expired:
                    if self._moving_name is not None:
                        break
                    source = self.config.downloads_dir / fname
                    if self._open_counts.get(fname, 0):
                        self.pending_files[fname] = time.time() + 30
                        continue
                    try:
                        stat = source.stat()
                        signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
                        if self._signatures.get(fname) not in (None, signature):
                            self.schedule_file(fname, notify=False)
                            continue
                    except OSError:
                        pass
                    self._start_move(fname, wake_write)

                self.save_state()

        finally:
            if self._move_thread is not None and self._move_thread.is_alive():
                self._move_thread.join(timeout=10)
                if self._move_thread.is_alive():
                    print("[WARNING] Transfer still blocked during shutdown; source will be rescanned on restart.", file=sys.stderr)
            self._collect_moves()
            self.cleanup_state()
            inotify_service.close()
            os.close(wake_read)
            os.close(wake_write)
