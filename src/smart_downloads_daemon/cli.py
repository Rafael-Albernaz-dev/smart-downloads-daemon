"""
Command-line interface for smart-downloads-daemon.
"""

import argparse
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

from . import __version__
from .config import DEFAULT_CONFIG_PATH, DaemonConfig
from .migrator import Migrator, format_bytes
from .watcher import Watcher

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
        print(f"  smart-downloads-daemon migrate --to \"{to_dir}\" --apply")
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

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="smart-downloads-daemon",
        description="Zero-dependency Linux inotify daemon for automated Downloads sorting with grace period and storage migration.",
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

    # Subcommand: config
    config_parser = subparsers.add_parser("config", help="View or modify daemon configuration")
    config_parser.add_argument("--show", action="store_true", help="Display current configuration and storage metrics")
    config_parser.add_argument("--set-destination", type=Path, help="Set and save new destination directory")
    config_parser.add_argument("--set-downloads", type=Path, help="Set and save new downloads directory")
    config_parser.add_argument("--set-grace-period", type=int, help="Set and save new grace period in seconds")
    config_parser.add_argument("--no-restart", action="store_true", help="Do not restart background service after changing config")

    # Subcommand: migrate
    migrate_parser = subparsers.add_parser("migrate", help="Batch migrate organized category folders across drives")
    migrate_parser.add_argument("--from", dest="from_dir", type=Path, help="Source directory (defaults to current destination_dir)")
    migrate_parser.add_argument("--to", dest="to_dir", type=Path, required=True, help="Target storage directory")
    migrate_parser.add_argument("--apply", action="store_true", help="Execute the migration (default is dry-run)")
    migrate_parser.add_argument("--dry-run", action="store_true", help="Simulate migration without modifying files (default)")
    migrate_parser.add_argument("--no-update-config", action="store_true", help="Do not update destination_dir in config.json after migration")
    migrate_parser.add_argument("-q", "--quiet", action="store_true", help="Suppress per-file progress output")

    args = parser.parse_args()

    config = DaemonConfig.load(args.config)
    if args.grace_period is not None:
        config.grace_period_seconds = max(0, args.grace_period)
    if args.destination is not None:
        config.destination_dir = args.destination.expanduser().resolve()

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

    # Standard Daemon / Scan-Once mode
    watcher = Watcher(config)

    if args.scan_once:
        print(f"[SCAN] Running one-time scan on {config.downloads_dir}...")
        watcher.process_existing_files()
        print("[SCAN] Completed.")
        sys.exit(0)

    stop_event = threading.Event()

    def on_signal(signum, frame):
        print("\n[DAEMON] Stopping daemon...")
        stop_event.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    watcher.run(stop_event=stop_event)

if __name__ == "__main__":
    main()
