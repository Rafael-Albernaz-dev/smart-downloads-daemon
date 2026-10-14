"""
Persistent activity history logging and inspection for smartdown.

Maintains an append-only JSON Lines file (~/.config/smart-downloads-daemon/history.jsonl)
recording all file moves, batch reorganizations, and storage migrations.
"""

from __future__ import annotations

import json
import fcntl
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from smart_downloads_daemon.config import DaemonConfig

MAX_HISTORY_ENTRIES = 50000
MAX_HISTORY_BYTES = 10 * 1024 * 1024
PRUNED_HISTORY_BYTES = 8 * 1024 * 1024


@contextmanager
def _history_lock(history_file: Path):
    lock_path = history_file.with_name(history_file.name + ".lock")
    with open(lock_path, "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield


def _reverse_lines(path: Path):
    with open(path, "rb") as stream:
        stream.seek(0, os.SEEK_END)
        position = stream.tell()
        pending = b""
        while position:
            size = min(64 * 1024, position)
            position -= size
            stream.seek(position)
            pending = stream.read(size) + pending
            parts = pending.split(b"\n")
            pending = parts.pop(0)
            for line in reversed(parts):
                if line:
                    yield line.decode("utf-8", errors="replace")
        if pending:
            yield pending.decode("utf-8", errors="replace")


def record_event(
    config: DaemonConfig,
    action: str,
    source: Union[str, Path],
    destination: Union[str, Path],
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
    size_bytes: Optional[int] = None,
    collision: bool = False,
    timestamp: Optional[str] = None,
) -> Dict[str, Any]:
    """Append a file migration or movement event to persistent history log."""
    ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    source_str = str(source.name if isinstance(source, Path) else source)
    dest_path = Path(destination)

    # Automatically derive category and subcategory if not explicitly provided
    if not category:
        try:
            rel = dest_path.relative_to(config.destination_dir)
            parts = rel.parts
            if parts:
                category = parts[0]
                if len(parts) > 2:
                    subcategory = parts[1]
        except Exception:
            pass

    entry: Dict[str, Any] = {
        "id": uuid.uuid4().hex,
        "timestamp": ts,
        "action": action.lower(),
        "source": source_str,
        "destination": str(dest_path),
        "category": category or "Outros",
        "subcategory": subcategory,
        "size_bytes": size_bytes,
        "collision": collision,
    }

    try:
        config.history_file.parent.mkdir(parents=True, exist_ok=True)
        with _history_lock(config.history_file):
            with open(config.history_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            _prune_history_unlocked(config.history_file)
    except Exception as e:
        print(f"[WARNING] Could not write history event: {e}", file=sys.stderr)

    return entry


def prune_history_if_needed(history_file: Path, max_entries: int = MAX_HISTORY_ENTRIES, force: bool = False) -> None:
    """Trim oldest lines if history exceeds max_entries or 10MB to prevent disk bloat."""
    if not history_file.is_file():
        return
    try:
        with _history_lock(history_file):
            _prune_history_unlocked(history_file, max_entries=max_entries, force=force)
    except Exception:
        pass


def _prune_history_unlocked(history_file: Path, max_entries: int = MAX_HISTORY_ENTRIES,
                            force: bool = False) -> None:
    if not force and history_file.stat().st_size < MAX_HISTORY_BYTES:
        return
    with open(history_file, "rb") as stream:
        lines = deque(stream, maxlen=max_entries)
    total = sum(map(len, lines))
    while lines and total > PRUNED_HISTORY_BYTES:
        total -= len(lines.popleft())
    temp = history_file.with_name(history_file.name + ".tmp")
    with open(temp, "wb") as stream:
        stream.writelines(lines)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(history_file)


def backfill_history_if_needed(config: DaemonConfig) -> int:
    """If history log does not exist, backfill past moves from systemd journal once."""
    if config.history_file.exists():
        return 0

    try:
        res = subprocess.run(
            [
                "journalctl",
                "-o",
                "short-iso",
                "--user",
                "-u",
                "smart-downloads-daemon",
                "--grep",
                r"\[OK\]",
                "-n",
                "500",
                "--no-pager",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode != 0 or not res.stdout:
            return 0

        pattern = re.compile(
            r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2})\s+\S+\s+(?:smart-downloads-daemon|smartdown)\[\d+\]:\s+\[OK\]\s+(.*?)\s+->\s+(.*)$"
        )
        entries: List[Dict[str, Any]] = []

        for line in res.stdout.splitlines():
            line = line.strip()
            match = pattern.match(line)
            if not match:
                continue

            iso_ts, source, dest = match.groups()
            try:
                dt = datetime.fromisoformat(iso_ts)
                fmt_ts = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                fmt_ts = iso_ts[:19].replace("T", " ")

            dest_path = Path(dest.strip())
            source_name = source.strip()

            cat = None
            subcat = None
            try:
                rel = dest_path.relative_to(config.destination_dir)
                parts = rel.parts
                if parts:
                    cat = parts[0]
                    if len(parts) > 2:
                        subcat = parts[1]
            except Exception:
                pass

            entries.append({
                "timestamp": fmt_ts,
                "action": "move",
                "source": source_name,
                "destination": str(dest_path),
                "category": cat or "Outros",
                "subcategory": subcat,
                "size_bytes": dest_path.stat().st_size if dest_path.exists() else None,
                "collision": ("_" in dest_path.stem and dest_path.name != source_name),
            })

        if entries:
            entries.sort(key=lambda x: x["timestamp"])
            config.history_file.parent.mkdir(parents=True, exist_ok=True)
            with _history_lock(config.history_file):
                if config.history_file.exists():
                    return 0
                with open(config.history_file, "w", encoding="utf-8") as f:
                    for entry in entries:
                        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
            return len(entries)

    except Exception:
        pass

    return 0


def load_history(
    config: DaemonConfig,
    limit: Optional[int] = None,
    action: Optional[str] = None,
    category: Optional[str] = None,
    reverse: bool = True,
) -> List[Dict[str, Any]]:
    """Load history records filtered by action or category."""
    backfill_history_if_needed(config)

    if not config.history_file.is_file():
        return []

    entries: List[Dict[str, Any]] = []
    action_norm = action.lower().strip() if action else None
    cat_norm = category.lower().strip() if category else None

    try:
        lines = _reverse_lines(config.history_file) if reverse else open(config.history_file, "r", encoding="utf-8")
        try:
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue

                if action_norm and data.get("action", "").lower() != action_norm:
                    continue
                if cat_norm and data.get("category", "").lower() != cat_norm:
                    continue

                entries.append(data)
                if limit is not None and limit > 0 and len(entries) >= limit:
                    break
        finally:
            if not reverse:
                lines.close()
    except Exception as e:
        print(f"[WARNING] Could not read history: {e}", file=sys.stderr)
        return []

    return entries


def clear_history(config: DaemonConfig) -> bool:
    """Clear all historical activity entries."""
    try:
        config.history_file.parent.mkdir(parents=True, exist_ok=True)
        with _history_lock(config.history_file):
            if config.history_file.is_file():
                config.history_file.write_text("", encoding="utf-8")
        return True
    except Exception as e:
        print(f"[ERROR] Could not clear history file: {e}", file=sys.stderr)
        return False


def format_bytes(num_bytes: Optional[int]) -> str:
    """Format bytes into human-readable string."""
    if num_bytes is None:
        return "-"
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num_bytes)
    idx = 0
    while size >= 1024.0 and idx < len(units) - 1:
        size /= 1024.0
        idx += 1
    return f"{size:.1f} {units[idx]}"


def format_log_entry(entry: Dict[str, Any]) -> str:
    """Format a single log entry for terminal display."""
    ts = entry.get("timestamp", "-")
    act = entry.get("action", "move").upper()
    cat = entry.get("category", "-")
    subcat = entry.get("subcategory")
    cat_display = f"{cat}/{subcat}" if subcat else cat

    source = entry.get("source", "-")
    dest = entry.get("destination", "-")
    dest_path = Path(dest)
    dest_short = f"{dest_path.parent.name}/{dest_path.name}"

    collision_badge = " [COLLISION]" if entry.get("collision") else ""
    size_str = format_bytes(entry.get("size_bytes"))

    return f"  {ts}  [{act:<8}]  {cat_display:<24}  '{source}' -> {dest_short} ({size_str}){collision_badge}"


def follow_history(
    config: DaemonConfig,
    limit: int = 10,
    action: Optional[str] = None,
    category: Optional[str] = None,
) -> None:
    """Follow and stream new log entries in real-time (like tail -f)."""
    backfill_history_if_needed(config)
    print("Following live smartdown activity logs (Ctrl+C to stop)...")
    print("-" * 78)

    initial = load_history(config, limit=limit, action=action, category=category, reverse=True)
    initial.reverse()
    for itm in initial:
        print(format_log_entry(itm))
    sys.stdout.flush()

    if not config.history_file.is_file():
        config.history_file.parent.mkdir(parents=True, exist_ok=True)
        config.history_file.touch()

    latest = load_history(config, limit=1, reverse=True)
    last_key = _history_entry_key(latest[0]) if latest else None
    stream = open(config.history_file, "r", encoding="utf-8")
    stream.seek(0, os.SEEK_END)
    try:
        while True:
            line = stream.readline()
            if line:
                try:
                    data = json.loads(line)
                    last_key = _history_entry_key(data)
                    if action and data.get("action", "").lower() != action.lower():
                        continue
                    if category and data.get("category", "").lower() != category.lower():
                        continue
                    print(format_log_entry(data))
                    sys.stdout.flush()
                except Exception:
                    pass
                continue

            try:
                current = config.history_file.stat()
                if current.st_ino != os.fstat(stream.fileno()).st_ino:
                    unseen = []
                    for raw in _reverse_lines(config.history_file):
                        try:
                            data = json.loads(raw)
                        except Exception:
                            continue
                        if _history_entry_key(data) == last_key:
                            break
                        unseen.append(data)
                    for data in reversed(unseen):
                        last_key = _history_entry_key(data)
                        if action and data.get("action", "").lower() != action.lower():
                            continue
                        if category and data.get("category", "").lower() != category.lower():
                            continue
                        print(format_log_entry(data))
                        sys.stdout.flush()
                    stream.close()
                    stream = open(config.history_file, "r", encoding="utf-8")
                    stream.seek(0, os.SEEK_END)
                elif stream.tell() > current.st_size:
                    stream.seek(0)
            except OSError:
                pass
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[LOGS] Stopped following.")
    finally:
        stream.close()


def _history_entry_key(entry: Dict[str, Any]) -> str:
    return entry.get("id") or json.dumps(entry, sort_keys=True, ensure_ascii=False)
