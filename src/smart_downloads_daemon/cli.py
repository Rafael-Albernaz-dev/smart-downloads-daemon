"""
Command-line interface for smart-downloads-daemon.
"""

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .config import DEFAULT_CONFIG_PATH, DaemonConfig
from .migrator import Migrator, format_bytes
from .organizer import Organizer
from .sorter import (
    get_destination_folder,
    has_sufficient_space,
    is_dev_workspace,
    is_folder_in_flight,
    is_valid_file,
    is_valid_item,
)
from .watcher import Watcher


def format_duration(seconds: float) -> str:
    """Format duration in seconds into human-readable string (e.g. '2m 15s', '45s', '1h 20m')."""
    total_secs = int(max(0, round(seconds)))
    if total_secs < 60:
        return f"{total_secs}s"
    hours = total_secs // 3600
    mins = (total_secs % 3600) // 60
    secs = total_secs % 60
    if hours > 0:
        return f"{hours}h {mins:02d}m"
    return f"{mins}m {secs:02d}s"


def acquire_instance_lock(lock_path: Path):
    """Attempt to acquire non-blocking exclusive flock on lockfile."""
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        f = open(lock_path, "w")
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        f.write(f"{os.getpid()}\n")
        f.flush()
        return f
    except (IOError, OSError, AttributeError):
        return None


def restart_user_service(service_name: str = "smart-downloads-daemon.service") -> bool:
    """Restart systemd user service if active."""
    try:
        check = subprocess.run(
            ["systemctl", "--user", "is-active", "--quiet", service_name],
            capture_output=True,
        )
        if check.returncode == 0:
            res = subprocess.run(
                ["systemctl", "--user", "restart", service_name],
                capture_output=True,
            )
            return res.returncode == 0
    except Exception:
        pass
    return False


def is_user_service_active(service_name: str = "smart-downloads-daemon.service") -> bool:
    """Check if systemd user service is currently active."""
    try:
        res = subprocess.run(
            ["systemctl", "--user", "is-active", "--quiet", service_name],
            capture_output=True,
        )
        return res.returncode == 0
    except Exception:
        return False


def get_service_info(service_name: str = "smart-downloads-daemon.service") -> Dict[str, Any]:
    """Retrieve detailed systemd user service state, PID, and uptime."""
    info = {"active": False, "pid": None, "uptime_seconds": None, "status_str": "inactive"}
    try:
        res = subprocess.run(
            ["systemctl", "--user", "show", service_name, "--property=ActiveState,MainPID"],
            capture_output=True,
            text=True,
        )
        if res.returncode == 0:
            props = {}
            for line in res.stdout.strip().splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    props[k] = v
            active_state = props.get("ActiveState", "inactive")
            info["active"] = (active_state == "active")
            info["status_str"] = active_state
            try:
                pid = int(props.get("MainPID", "0"))
                if pid > 0:
                    info["pid"] = pid
                    ps_res = subprocess.run(
                        ["ps", "-p", str(pid), "-o", "etimes="],
                        capture_output=True,
                        text=True,
                    )
                    if ps_res.returncode == 0 and ps_res.stdout.strip():
                        info["uptime_seconds"] = int(ps_res.stdout.strip())
            except Exception:
                pass
    except Exception:
        pass
    return info


def show_status(config: DaemonConfig, as_json: bool = False) -> None:
    """Display comprehensive status of daemon service, pause state, disk health, and queue."""
    svc_info = get_service_info()
    is_paused = config.is_paused()
    pause_info = config.get_pause_info()
    dest_available = config.is_destination_available()
    dest_device = config.get_destination_device()
    dl_info = config.get_storage_info(config.downloads_dir)
    dest_info = config.get_storage_info(config.destination_dir)

    # Read state.json if present
    state_data: Dict[str, Any] = {}
    if config.state_file.is_file():
        try:
            with open(config.state_file, "r", encoding="utf-8") as f:
                state_data = json.load(f)
        except Exception:
            pass

    now = time.time()
    pending_items = []
    ignored_items = []

    if config.downloads_dir.is_dir():
        try:
            for item in sorted(config.downloads_dir.iterdir(), key=lambda p: p.name.lower()):
                if not (item.is_file() or item.is_dir()):
                    continue

                if not is_valid_item(item.name, config, base_dir=config.downloads_dir):
                    if item.is_dir() and is_dev_workspace(item):
                        ignored_items.append(f"{item.name}/ (active dev workspace)")
                    elif item.is_dir() and is_folder_in_flight(item, config):
                        ignored_items.append(f"{item.name}/ (in-flight download)")
                    else:
                        ignored_items.append(item.name)
                    continue

                target_folder_path = get_destination_folder(item.name, config, source_path=item)
                target_folder = target_folder_path.name

                try:
                    if item.is_dir():
                        item_size = sum(f.stat().st_size for f in item.rglob("*") if f.is_file())
                    else:
                        item_size = item.stat().st_size
                except Exception:
                    item_size = 0

                # Look up countdown in state_data or estimate from mtime
                state_pending = state_data.get("pending_files", {}).get(item.name)
                if state_pending:
                    due = state_pending.get("due_time", now)
                    remaining = max(0.0, due - now)
                else:
                    if item.is_dir():
                        try:
                            all_mtimes = [item.stat().st_mtime] + [f.stat().st_mtime for f in item.rglob("*")]
                            mtime = max(all_mtimes)
                        except Exception:
                            mtime = item.stat().st_mtime
                    else:
                        mtime = item.stat().st_mtime
                    age = now - mtime
                    remaining = max(0.0, config.grace_period_seconds - age)

                # Determine file state label
                if is_paused:
                    status_lbl = "HELD (daemon is paused)"
                elif not dest_available:
                    status_lbl = "HELD (destination drive unavailable)"
                elif not has_sufficient_space(target_folder_path, item_size):
                    status_lbl = "HELD (insufficient disk space on destination)"
                elif remaining > 0:
                    status_lbl = f"{format_duration(remaining)} remaining -> {target_folder}/"
                else:
                    status_lbl = f"Ready to move -> {target_folder}/"

                display_name = f"{item.name}/" if item.is_dir() else item.name
                pending_items.append({
                    "name": display_name,
                    "target_category": target_folder,
                    "remaining_seconds": round(remaining, 1),
                    "status_label": status_lbl,
                })
        except Exception:
            pass

    recent_moves = state_data.get("recent_moves", [])
    if not recent_moves:
        try:
            from smart_downloads_daemon.history import load_history
            hist = load_history(config, limit=5, reverse=True)
            recent_moves = [
                {
                    "file": h.get("source", ""),
                    "destination": h.get("destination", ""),
                    "timestamp": h.get("timestamp", ""),
                    "action": h.get("action", "move"),
                }
                for h in reversed(hist)
            ]
        except Exception:
            pass

    if as_json:
        data = {
            "service": svc_info,
            "paused": is_paused,
            "pause_info": pause_info,
            "grace_period_seconds": config.grace_period_seconds,
            "downloads": {
                "path": str(config.downloads_dir),
                "storage": dl_info,
            },
            "destination": {
                "path": str(config.destination_dir),
                "available": dest_available,
                "device": str(dest_device.resolve()) if dest_device else None,
                "storage": dest_info,
            },
            "queue": {
                "pending_files": pending_items,
                "ignored_files": ignored_items,
            },
            "recent_moves": recent_moves,
        }
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return

    print("=" * 64)
    print(" SMART DOWNLOADS DAEMON - STATUS")
    print("=" * 64)

    # 1. Daemon Service & Operating Mode
    print("Daemon Service:")
    if svc_info["active"]:
        pid_str = f"PID: {svc_info['pid']}" if svc_info['pid'] else "running"
        uptime_str = f" | Uptime: {format_duration(svc_info['uptime_seconds'])}" if svc_info.get("uptime_seconds") is not None else ""
        print(f"  • Service:      active (running) [{pid_str}{uptime_str}]")
    else:
        print("  • Service:      inactive / stopped")

    mins = round(config.grace_period_seconds / 60, 1)
    if is_paused:
        pause_date = pause_info.get("paused_at", "recently")[:19].replace("T", " ") if pause_info else ""
        reason_str = f" ({pause_info.get('reason')})" if pause_info and pause_info.get("reason") else ""
        print(f"  • Mode:         ⏸  PAUSED since {pause_date}{reason_str}")
        print("                  (Automated file sorting is currently SUSPENDED)")
    else:
        print(f"  • Mode:         ●  ACTIVE (Sorting enabled - {mins} min grace period)")

    print("")
    print("Storage & Directories:")

    # Downloads directory
    if dl_info:
        dl_free = format_bytes(dl_info["free_bytes"])
        dl_total = format_bytes(dl_info["total_bytes"])
        dl_storage = f"{dl_free} free of {dl_total}"
    else:
        dl_storage = "Accessible"
    print(f"  • Downloads:    {config.downloads_dir} [{dl_storage}]")

    # Destination directory
    if dest_available:
        if dest_info:
            dest_free = format_bytes(dest_info["free_bytes"])
            dest_total = format_bytes(dest_info["total_bytes"])
            dest_storage = f"Accessible [{dest_free} free of {dest_total} - {dest_info['percent_used']}% used]"
        else:
            dest_storage = "Accessible [OK]"
        print(f"  • Destination:  {config.destination_dir} [{dest_storage}]")
    else:
        print(f"  • Destination:  {config.destination_dir} [UNAVAILABLE / UNMOUNTED ⚠️]")
        if dest_device:
            try:
                resolved_dev = dest_device.resolve()
                print(f"                  Device detected: {resolved_dev} ({dest_device.name})")
                print("                  Tip: Run 'smartdown mount' to mount it automatically.")
            except Exception:
                pass
        else:
            print("                  Storage device is not mounted or not connected.")
        print("                  Note: Files remain safe in Downloads until drive is available.")

    print("")
    print("Downloads Queue:")
    if not pending_items and not ignored_items:
        print("  • No files currently in Downloads.")
    else:
        if pending_items:
            print(f"  • Pending Files ({len(pending_items)}):")
            for item in pending_items:
                print(f"    - '{item['name']}': {item['status_label']}")
        if ignored_items:
            print(f"  • Ignored / Incomplete ({len(ignored_items)}):")
            for name in ignored_items[:5]:
                print(f"    - '{name}' (in-flight download or temporary file)")
            if len(ignored_items) > 5:
                print(f"    ... and {len(ignored_items) - 5} more.")

    if recent_moves:
        print("")
        print("Recent Activity (Last Organized):")
        for mv in reversed(recent_moves[-5:]):
            ts = mv.get("timestamp", "")
            fname = mv.get("file", "")
            dest_path = mv.get("destination", "")
            act = mv.get("action", "move").upper()
            dest_name = Path(dest_path).parent.name if dest_path else ""
            print(f"  • {ts} [{act}]: '{fname}' -> {dest_name}/")

    print("-" * 64)
    if is_paused:
        print("👉 Tip: Run 'smartdown resume' to reactivate automated sorting.")
    elif not dest_available:
        print("👉 Tip: Run 'smartdown mount' to connect the storage destination.")
    elif not svc_info["active"]:
        print("👉 Tip: Run 'systemctl --user start smart-downloads-daemon' to start daemon.")
    else:
        print("💡 Controls: 'smartdown pause' | 'smartdown logs' (history) | 'status' (refresh)")
    print("=" * 64)


def run_pause_cli(config: DaemonConfig, reason: str = "") -> int:
    """Pause file sorting."""
    config.set_paused(True, reason=reason)
    print("=" * 64)
    print(" ⏸  SMART DOWNLOADS DAEMON - PAUSED")
    print("=" * 64)
    print("Automated sorting has been suspended.")
    print(f"Files downloaded to '{config.downloads_dir}' will remain in place.")
    if reason:
        print(f"Reason: {reason}")
    print("")
    print("To resume automated sorting at any time, run:")
    print("  smartdown resume")
    print("=" * 64)
    return 0


def run_resume_cli(config: DaemonConfig) -> int:
    """Resume file sorting."""
    config.set_paused(False)
    print("=" * 64)
    print(" ▶  SMART DOWNLOADS DAEMON - RESUMED")
    print("=" * 64)
    print("Automated sorting has been reactivated.")
    print(f"Files in '{config.downloads_dir}' will be organized according to the grace period.")
    print("")
    print("To check queue status, run:")
    print("  smartdown status")
    print("=" * 64)
    return 0


def run_mount_cli(config: DaemonConfig) -> int:
    """Attempt to mount destination directory if unmounted."""
    print("=" * 64)
    print(" SMART DOWNLOADS DAEMON - MOUNT STORAGE")
    print("=" * 64)
    print(f"Target destination: {config.destination_dir}")

    if config.is_destination_available():
        info = config.get_storage_info(config.destination_dir)
        free_str = format_bytes(info["free_bytes"]) if info else "Accessible"
        print(f"✓ Destination is already mounted and accessible! ({free_str} free)")
        print("=" * 64)
        return 0

    dev = config.get_destination_device()
    if dev:
        try:
            print(f"• Found storage device: {dev.resolve()} ({dev.name})")
        except Exception:
            print(f"• Found storage device: {dev}")
    else:
        print("• Searching for partition...")

    print("• Attempting auto-mount...")
    if config.try_automount_destination():
        info = config.get_storage_info(config.destination_dir)
        free_str = format_bytes(info["free_bytes"]) if info else "Accessible"
        print(f"✓ Successfully mounted destination to {config.destination_dir}!")
        print(f"  Free space: {free_str}")
        print("  Daemon will now automatically process queued files.")
        print("=" * 64)
        return 0
    else:
        print("! Could not auto-mount destination directory.")
        if dev:
            print(f"  Try running manually: udisksctl mount -b {dev}")
        else:
            print("  Please make sure the drive is connected or check 'lsblk'.")
        print("=" * 64)
        return 1


def run_logs_cli(
    config: DaemonConfig,
    limit: int = 25,
    show_all: bool = False,
    follow: bool = False,
    action: Optional[str] = None,
    category: Optional[str] = None,
    clear: bool = False,
    as_json: bool = False,
) -> int:
    """Display or stream activity logs."""
    from smart_downloads_daemon.history import (
        clear_history,
        follow_history,
        format_log_entry,
        load_history,
    )

    if clear:
        if clear_history(config):
            print("✓ History logs cleared successfully.")
            return 0
        else:
            return 1

    if follow:
        follow_history(config, limit=limit, action=action, category=category)
        return 0

    effective_limit = None if show_all or limit <= 0 else limit
    entries = load_history(
        config,
        limit=effective_limit,
        action=action,
        category=category,
        reverse=True,
    )

    if as_json:
        print(json.dumps(entries, indent=2, ensure_ascii=False))
        return 0

    print("=" * 80)
    print(" SMARTDOWN - RECENT ACTIVITY LOGS")
    print("=" * 80)

    filters = []
    if action:
        filters.append(f"action={action}")
    if category:
        filters.append(f"category={category}")
    filter_desc = f" [Filters: {', '.join(filters)}]" if filters else ""

    if not entries:
        print(f"No activity logs found.{filter_desc}")
        print("=" * 80)
        return 0

    total_str = f"Showing {len(entries)} entries{filter_desc}:"
    print(total_str)
    print("")
    print(f"  {'TIMESTAMP':<19}  {'ACTION':<10}  {'CATEGORY':<24}  {'FILE -> DESTINATION'}")
    print("  " + "-" * 76)

    for entry in entries:
        print(format_log_entry(entry))

    print("-" * 80)
    print("💡 Tips: 'smartdown logs -n 50' | 'smartdown logs -f' (live follow) | 'smartdown logs --json'")
    print("=" * 80)
    return 0


def show_config(config: DaemonConfig, config_path: Optional[Path] = None) -> None:
    """Display active daemon configuration and storage metrics."""
    cfg_file = config_path or DEFAULT_CONFIG_PATH
    file_status = "Saved on disk" if cfg_file.is_file() else "Default built-in (not yet customized)"

    dl_info = config.get_storage_info(config.downloads_dir)
    dest_info = config.get_storage_info(config.destination_dir)
    service_active = is_user_service_active()

    print("=" * 64)
    print(" SMART DOWNLOADS DAEMON - CONFIGURATION")
    print("=" * 64)
    print(f"Config File:      {cfg_file} ({file_status})")
    print("")
    print("Directories & Storage:")

    # Downloads directory info
    if dl_info:
        dl_free = format_bytes(dl_info["free_bytes"])
        dl_total = format_bytes(dl_info["total_bytes"])
        dl_status = f"{dl_free} free of {dl_total} ({dl_info['percent_used']}% used)"
    else:
        dl_status = "Storage metric unavailable"
    print(f"  • Downloads:    {config.downloads_dir} [{dl_status}]")

    # Destination directory info
    dest_ok = "Accessible [OK]" if config.is_destination_available() else "UNAVAILABLE / UNMOUNTED [WARN]"
    if dest_info:
        dest_free = format_bytes(dest_info["free_bytes"])
        dest_total = format_bytes(dest_info["total_bytes"])
        dest_storage = f"{dest_free} free of {dest_total} ({dest_info['percent_used']}% used) - {dest_ok}"
    else:
        dest_storage = dest_ok
    print(f"  • Destination:  {config.destination_dir} [{dest_storage}]")

    print("")
    print("Settings:")
    mins = round(config.grace_period_seconds / 60, 1)
    print(f"  • Grace Period: {config.grace_period_seconds}s ({mins} min)")
    print(f"  • Categories:   {', '.join(config.categories.keys())}, Outros")
    print(f"  • Ignored Exts: {', '.join(sorted(config.ignore_extensions))}")
    print(f"  • Notification: {'Enabled' if config.notifications_enabled else 'Disabled'}")
    print("")
    print("Systemd Service:")
    svc_status = "active (running)" if service_active else "inactive / disabled"
    print(f"  • Status:       {svc_status}")
    print("=" * 64)


def run_migration_cli(
    from_dir: Path,
    to_dir: Path,
    dry_run: bool,
    update_config: bool,
    quiet: bool,
    config: DaemonConfig,
    config_path: Optional[Path] = None,
) -> int:
    """Execute or simulate batch category migration between directories."""
    migrator = Migrator(config)
    plan = migrator.create_plan(from_dir=from_dir, to_dir=to_dir)

    mode_title = "BATCH MIGRATION (DRY RUN)" if dry_run else "BATCH MIGRATION"
    print("=" * 64)
    print(f" SMART DOWNLOADS DAEMON - {mode_title}")
    print("=" * 64)
    print(f"Source:       {plan.from_dir}")
    print(f"Destination:  {plan.to_dir}")
    print("")

    if not plan.categories_found:
        print(f"No organized category folders found in {plan.from_dir}.")
        print("Nothing to migrate.")
        return 0

    print("Categories detected:")
    for cat in plan.categories_found:
        summary = plan.category_summary[cat]
        cnt = summary["files"]
        size_str = format_bytes(summary["bytes"])
        print(f"  • {cat:<14} {cnt:>4} files ({size_str})")

    total_size_str = format_bytes(plan.total_bytes)
    print("-" * 64)
    print(f"Total to migrate: {plan.total_files} files ({total_size_str})")

    to_storage = config.get_storage_info(to_dir)
    if to_storage:
        print(f"Destination disk: {format_bytes(to_storage['free_bytes'])} free space available.")
    print("")

    if dry_run:
        print("[DRY RUN] No files were moved. To execute this migration, re-run with --apply:")
        print(f"  smartdown migrate --to \"{to_dir}\" --apply")
        print("=" * 64)
        return 0

    print("Executing migration...")
    last_cat = None

    def on_progress(item, current, total):
        nonlocal last_cat
        if not quiet:
            if item.category != last_cat:
                last_cat = item.category
                print(f"\nMoving category [{item.category}]:")
            pct = int((current / total) * 100)
            print(f"  [{pct:>3}%] {item.category}/{item.relative_path}")
            sys.stdout.flush()

    result = migrator.execute_plan(plan, dry_run=False, progress_callback=on_progress)
    print("")
    print("-" * 64)
    print("Migration Summary:")
    print(f"  ✓ Files moved:            {result.files_moved} / {result.total_files_planned}")
    print(f"  ✓ Space freed on source:  {format_bytes(result.bytes_moved)}")
    if result.collisions_resolved > 0:
        print(f"  ✓ Collisions protected:   {result.collisions_resolved} (renamed safely with timestamp)")
    if result.directories_cleaned > 0:
        print(f"  ✓ Empty folders cleaned:  {result.directories_cleaned}")

    if result.errors:
        print(f"  ! Errors encountered:     {len(result.errors)}")
        for err in result.errors[:5]:
            print(f"    - {err}")
        if len(result.errors) > 5:
            print(f"    ... and {len(result.errors) - 5} more.")

    if update_config:
        config.destination_dir = to_dir
        saved_path = config.save(config_path)
        print(f"  ✓ Updated daemon config:   destination_dir -> {to_dir}")
        print(f"    (Saved to {saved_path})")

        if restart_user_service():
            print("  ✓ Restarted smart-downloads-daemon.service with new destination!")
        else:
            print("  • Note: Restart the service when ready: 'systemctl --user restart smart-downloads-daemon'")

    print("=" * 64)
    return 0 if not result.errors else 1


def run_organize_cli(
    target_dir: Optional[Path],
    dry_run: bool,
    quiet: bool,
    config: DaemonConfig,
) -> int:
    """Execute or simulate batch reorganization of existing category files into subcategories."""
    organizer = Organizer(config)
    plan = organizer.create_plan(target_dir=target_dir)

    mode_title = "SMART REORGANIZATION (DRY RUN)" if dry_run else "SMART REORGANIZATION"
    print("=" * 64)
    print(f" SMART DOWNLOADS DAEMON - {mode_title}")
    print("=" * 64)
    print(f"Target Directory: {plan.target_dir}")
    print("")

    if not plan.items:
        print("✓ All files are already organized into their appropriate subcategories!")
        print("  Nothing to reorganize.")
        print("=" * 64)
        return 0

    print("Reorganization Plan:")
    for cat, info in plan.category_summary.items():
        cnt = info["total_files"]
        size_str = format_bytes(info["total_bytes"])
        print(f"  • Category [{cat}] - {cnt} files ({size_str}):")
        for subcat, scnt in sorted(info["subcategories"].items(), key=lambda x: -x[1]):
            print(f"      -> {subcat:<26}: {scnt:>4} files")

    print("-" * 64)
    print(f"Total to organize: {plan.total_files} files ({format_bytes(plan.total_bytes)})")
    print("")

    if dry_run:
        print("[DRY RUN] No files were moved. To execute this reorganization, re-run with --apply:")
        print(f"  smartdown organize --apply")
        print("=" * 64)
        return 0

    print("Executing reorganization...")
    last_cat = None

    def on_progress(item, current, total):
        nonlocal last_cat
        if not quiet:
            if item.category != last_cat:
                last_cat = item.category
                print(f"\nOrganizing category [{item.category}]:")
            pct = int((current / total) * 100)
            print(f"  [{pct:>3}%] {item.category}/{item.source_path.name} -> {item.target_dir.name}/")
            sys.stdout.flush()

    result = organizer.execute_plan(plan, dry_run=False, progress_callback=on_progress)
    print("")
    print("-" * 64)
    print("Reorganization Summary:")
    print(f"  ✓ Files organized:        {result.files_moved} / {result.total_files_planned}")
    print(f"  ✓ Data volume processed:  {format_bytes(result.bytes_moved)}")
    if result.collisions_resolved > 0:
        print(f"  ✓ Collisions protected:   {result.collisions_resolved} (safely renamed with timestamp)")
    if result.errors:
        print(f"  ! Errors:                 {len(result.errors)}")
        for err in result.errors[:5]:
            print(f"    - {err}")
    print("=" * 64)
    return 0 if not result.errors else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="smartdown",
        description="Zero-dependency Linux inotify daemon for automated Downloads sorting with grace period, pause control, and storage management.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to custom JSON configuration file.",
    )
    parser.add_argument(
        "--grace-period",
        type=int,
        help="Override grace period in seconds (default: 300).",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        help="Override destination directory for this session.",
    )
    parser.add_argument(
        "-f", "--foreground",
        action="store_true",
        help="Run daemon in foreground (even if background service is running).",
    )
    parser.add_argument(
        "--scan-once",
        action="store_true",
        help="Perform a single scan to organize eligible files, then exit immediately.",
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # Subcommand: status
    status_parser = subparsers.add_parser("status", help="Show daemon status, pause state, storage health, and queue")
    status_parser.add_argument("--json", action="store_true", help="Output status in JSON format")

    # Subcommand: pause
    pause_parser = subparsers.add_parser("pause", help="Pause automated file organization (hold files in Downloads)")
    pause_parser.add_argument("--reason", type=str, default="", help="Optional reason for pausing")

    # Subcommand: resume & unpause
    subparsers.add_parser("resume", help="Resume automated file organization")
    subparsers.add_parser("unpause", help="Alias for 'resume'")

    # Subcommand: mount
    subparsers.add_parser("mount", help="Attempt to mount destination drive if unmounted")

    # Subcommand: organize
    organize_parser = subparsers.add_parser("organize", help="Reorganize existing files into smart subcategories")
    organize_parser.add_argument("--dir", dest="target_dir", type=Path, help="Target storage directory (defaults to current destination_dir)")
    organize_parser.add_argument("--apply", action="store_true", help="Execute reorganization (default is dry-run)")
    organize_parser.add_argument("--dry-run", action="store_true", help="Simulate reorganization without modifying files (default)")
    organize_parser.add_argument("-q", "--quiet", action="store_true", help="Suppress per-file progress output")

    # Subcommand: config
    config_parser = subparsers.add_parser("config", help="View or modify daemon configuration")
    config_parser.add_argument("--show", action="store_true", help="Display current configuration and storage metrics")
    config_parser.add_argument("--set-destination", type=Path, help="Set and save new destination directory")
    config_parser.add_argument("--set-downloads", type=Path, help="Set and save new downloads directory")
    config_parser.add_argument("--set-grace-period", type=int, help="Set and save new grace period in seconds")
    config_parser.add_argument("--set-notifications", choices=["on", "off"], help="Enable or disable desktop notifications (on/off)")
    config_parser.add_argument("--no-restart", action="store_true", help="Do not restart background service after changing config")

    # Subcommand: migrate
    migrate_parser = subparsers.add_parser("migrate", help="Batch migrate organized category folders across drives")
    migrate_parser.add_argument("--from", dest="from_dir", type=Path, help="Source directory (defaults to current destination_dir)")
    migrate_parser.add_argument("--to", dest="to_dir", type=Path, required=True, help="Target storage directory")
    migrate_parser.add_argument("--apply", action="store_true", help="Execute the migration (default is dry-run)")
    migrate_parser.add_argument("--dry-run", action="store_true", help="Simulate migration without modifying files (default)")
    migrate_parser.add_argument("--no-update-config", action="store_true", help="Do not update destination_dir in config.json after migration")
    migrate_parser.add_argument("-q", "--quiet", action="store_true", help="Suppress per-file progress output")

    # Subcommand: logs & log
    logs_parser = subparsers.add_parser("logs", aliases=["log"], help="Show recent file organization and migration history")
    logs_parser.add_argument("-n", "--limit", type=int, default=25, help="Number of log entries to display (default: 25)")
    logs_parser.add_argument("--all", action="store_true", help="Display all historical logs")
    logs_parser.add_argument("-f", "--follow", action="store_true", help="Follow live activity logs in real-time (tail -f)")
    logs_parser.add_argument("--action", type=str, choices=["move", "organize", "migrate"], help="Filter by action type")
    logs_parser.add_argument("--category", type=str, help="Filter by category (e.g. PDF, Planilhas)")
    logs_parser.add_argument("--clear", action="store_true", help="Clear historical logs")
    logs_parser.add_argument("--json", action="store_true", help="Output logs in JSON format")

    args = parser.parse_args()

    config = DaemonConfig.load(args.config)
    if args.grace_period is not None:
        config.grace_period_seconds = max(0, args.grace_period)
    if args.destination is not None:
        config.destination_dir = args.destination.expanduser().resolve()

    # Handle 'status' command
    if args.command == "status":
        show_status(config, as_json=args.json)
        return

    # Handle 'pause' command
    if args.command == "pause":
        sys.exit(run_pause_cli(config, reason=args.reason))

    # Handle 'resume' / 'unpause' command
    if args.command in ("resume", "unpause"):
        sys.exit(run_resume_cli(config))

    # Handle 'mount' command
    if args.command == "mount":
        sys.exit(run_mount_cli(config))

    # Handle 'organize' command
    if args.command == "organize":
        target = (args.target_dir or config.destination_dir).expanduser().resolve()
        dry_run = not args.apply
        exit_code = run_organize_cli(
            target_dir=target,
            dry_run=dry_run,
            quiet=args.quiet,
            config=config,
        )
        sys.exit(exit_code)

    # Handle 'config' command
    if args.command == "config":
        modified = False
        if args.set_destination is not None:
            config.destination_dir = args.set_destination.expanduser().resolve()
            modified = True
            print(f"✓ Updated destination_dir to: {config.destination_dir}")

        if args.set_downloads is not None:
            config.downloads_dir = args.set_downloads.expanduser().resolve()
            modified = True
            print(f"✓ Updated downloads_dir to: {config.downloads_dir}")

        if args.set_grace_period is not None:
            config.grace_period_seconds = max(0, args.set_grace_period)
            modified = True
            print(f"✓ Updated grace_period_seconds to: {config.grace_period_seconds}")

        if args.set_notifications is not None:
            config.notifications_enabled = (args.set_notifications == "on")
            modified = True
            state_str = "Enabled" if config.notifications_enabled else "Disabled"
            print(f"✓ Updated notifications to: {state_str}")

        if modified:
            saved_file = config.save(args.config)
            print(f"✓ Configuration saved to {saved_file}")
            if not args.no_restart and restart_user_service():
                print("✓ Restarted smart-downloads-daemon.service with new settings!")
            elif not args.no_restart:
                print("• Service is not currently active. New settings will apply on next start.")
        else:
            show_config(config, args.config)
        return

    # Handle 'migrate' command
    if args.command == "migrate":
        from_dir = (args.from_dir or config.destination_dir).expanduser().resolve()
        to_dir = args.to_dir.expanduser().resolve()
        dry_run = not args.apply
        update_cfg = not args.no_update_config and not dry_run

        exit_code = run_migration_cli(
            from_dir=from_dir,
            to_dir=to_dir,
            dry_run=dry_run,
            update_config=update_cfg,
            quiet=args.quiet,
            config=config,
            config_path=args.config,
        )
        sys.exit(exit_code)

    # Handle 'logs' / 'log' command
    if args.command in ("logs", "log"):
        sys.exit(
            run_logs_cli(
                config=config,
                limit=args.limit,
                show_all=args.all,
                follow=args.follow,
                action=args.action,
                category=args.category,
                clear=args.clear,
                as_json=args.json,
            )
        )

    # Standard Daemon / Scan-Once mode
    if args.scan_once:
        print(f"[SCAN] Running one-time scan on {config.downloads_dir}...")
        watcher = Watcher(config)
        watcher.process_existing_files()
        print("[SCAN] Completed.")
        sys.exit(0)

    # If invoked directly with no subcommands and not launched by systemd:
    if "INVOCATION_ID" not in os.environ and is_user_service_active() and not args.foreground:
        print("=" * 64)
        print(" smartdown is already running in the background.")
        print("=" * 64)
        print("Useful commands:")
        print("  smartdown status    # View current state, queue, and destination health")
        print("  smartdown logs      # View recent migration and organization history")
        print("  smartdown pause     # Temporarily hold downloads (suspend sorting)")
        print("  smartdown resume    # Resume automated file sorting")
        print("  smartdown mount     # Auto-mount destination drive if unmounted")
        print("  smartdown organize  # Reorganize categories into smart subdirectories")
        print("  smartdown config    # View or modify configuration")
        print("  smartdown migrate   # Move categories across storage")
        print("")
        print("To stop or restart the background service:")
        print("  systemctl --user stop smart-downloads-daemon")
        print("  systemctl --user restart smart-downloads-daemon")
        print("")
        print("To run in foreground anyway:")
        print("  smartdown --foreground")
        print("=" * 64)
        sys.exit(0)

    lock_fp = acquire_instance_lock(config.lock_file)
    if lock_fp is None:
        print(
            "[ERROR] Another foreground instance of smartdown is already running.",
            file=sys.stderr,
        )
        sys.exit(1)

    watcher = Watcher(config)
    stop_event = threading.Event()

    def on_signal(signum, frame):
        print("\n[DAEMON] Stopping daemon...")
        stop_event.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    try:
        watcher.run(stop_event=stop_event)
    finally:
        try:
            lock_fp.close()
            if config.lock_file.is_file():
                config.lock_file.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    main()

