"""
Desktop notification module for smart-downloads-daemon.

Dispatches non-blocking visual notifications on Linux desktop environments
using notify-send with resilient environment discovery and fallback handling.
"""

import os
import queue
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Optional

from .config import DaemonConfig

_notification_queue = queue.Queue(maxsize=128)
_worker_lock = threading.Lock()
_worker = None


def _notification_worker() -> None:
    while True:
        args = _notification_queue.get()
        try:
            send_notification(*args)
        finally:
            _notification_queue.task_done()


def dispatch_notification(title: str, message: str, icon: str, urgency: str,
                          config: Optional[DaemonConfig]) -> bool:
    """Queue a best-effort notification without waiting for the desktop bus."""
    global _worker
    if config is not None and not config.notifications_enabled:
        return False
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_notification_worker, daemon=True, name="smartdown-notify")
            _worker.start()
    try:
        _notification_queue.put_nowait((title, message, icon, urgency, config))
        return True
    except queue.Full:
        return False


def _detect_display_env() -> dict:
    """Use the actual session environment and the current user's D-Bus bus."""
    env = os.environ.copy()
    uid = os.getuid()
    run_user = Path(f"/run/user/{uid}")

    if "XDG_RUNTIME_DIR" not in env:
        if run_user.is_dir():
            env["XDG_RUNTIME_DIR"] = str(run_user)

    # If DBUS_SESSION_BUS_ADDRESS is missing, check default user bus
    if "DBUS_SESSION_BUS_ADDRESS" not in env:
        user_bus = run_user / "bus"
        if user_bus.exists():
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={user_bus}"

    return env


def send_notification(
    title: str,
    message: str,
    icon: str = "folder-download",
    urgency: str = "normal",
    config: Optional[DaemonConfig] = None,
) -> bool:
    """
    Send a desktop notification via notify-send.
    Returns True if sent successfully, False otherwise.
    Never raises exceptions.
    """
    if config is not None and not config.notifications_enabled:
        return False

    notify_bin = shutil.which("notify-send")
    if not notify_bin:
        return False

    env = _detect_display_env()

    cmd = [
        notify_bin,
        "-a", "smartdown",
        "-i", icon,
        "-u", urgency,
        "-t", "4000",
        title,
        message,
    ]

    try:
        res = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            timeout=2.0,
        )
        return res.returncode == 0
    except Exception:
        return False


def notify_organized(
    source_name: str,
    dest_path: Path,
    is_dir: bool = False,
    config: Optional[DaemonConfig] = None,
) -> bool:
    """
    Notify the user that a file or folder was organized.
    """
    item_type = "Pasta" if is_dir else "Arquivo"
    title = "Smart Downloads"

    # Display relative path or destination directory name
    if config:
        try:
            rel = dest_path.relative_to(config.destination_dir)
            dest_display = str(rel)
        except Exception:
            dest_display = dest_path.name
    else:
        dest_display = dest_path.name

    message = f"{item_type} organizado:\n{source_name} ➔ {dest_display}"
    icon = "folder" if is_dir else "document-save"

    return dispatch_notification(
        title=title,
        message=message,
        icon=icon,
        urgency="normal",
        config=config,
    )


def notify_scheduled(
    source_name: str,
    delay_minutes: float,
    is_dir: bool = False,
    config: Optional[DaemonConfig] = None,
) -> bool:
    """
    Notify the user that a downloaded file or directory is queued in Downloads.
    """
    item_type = "Pasta" if is_dir else "Arquivo"
    title = "Smart Downloads"
    mins = round(delay_minutes, 1)
    message = f"{item_type} detectado:\n{source_name} (organização em {mins} min)"
    icon = "folder" if is_dir else "document-save"

    return dispatch_notification(
        title=title,
        message=message,
        icon=icon,
        urgency="low",
        config=config,
    )
