"""
Command-line interface for smart-downloads-daemon.
"""

import argparse
import signal
import sys
import threading
from pathlib import Path

from . import __version__
from .config import DaemonConfig
from .watcher import Watcher

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="smart-downloads-daemon",
        description="Zero-dependency Linux inotify daemon for automated Downloads sorting with grace period.",
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
        "--scan-once",
        action="store_true",
        help="Perform a single scan to organize eligible files, then exit immediately.",
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    args = parser.parse_args()

    config = DaemonConfig.load(args.config)
    if args.grace_period is not None:
        config.grace_period_seconds = max(0, args.grace_period)

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
