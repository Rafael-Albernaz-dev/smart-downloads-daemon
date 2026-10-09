"""
Desktop notification module for smart-downloads-daemon.

Dispatches non-blocking visual notifications on Linux desktop environments
using notify-send with resilient environment discovery and fallback handling.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .config import DaemonConfig


def _detect_display_env() -> dict:
    """Prepare environment variables for notify-send with display discovery."""
    env = os.environ.copy()

    # If DBUS_SESSION_BUS_ADDRESS is missing, check default user bus
    if "DBUS_SESSION_BUS_ADDRESS" not in env:
        uid = os.getuid()
        user_bus = Path(f"/run/user/{uid}/bus")
        if user_bus.exists():
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={user_bus}"

    # If neither DISPLAY nor WAYLAND_DISPLAY is set (common in systemd --user units),
    # detect active X11 display socket in /tmp/.X11-unix/
    if "DISPLAY" not in env and "WAYLAND_DISPLAY" not in env:
        x11_dir = Path("/tmp/.X11-unix")
        if x11_dir.is_dir():
            try:
                sockets = sorted(x11_dir.glob("X*"))
                if sockets:
                    # e.g. /tmp/.X11-unix/X0 -> :0, /tmp/.X11-unix/X1 -> :1
                    display_num = sockets[0].name.replace("X", ":")
                    env["DISPLAY"] = display_num
            except Exception:
                pass
        if "DISPLAY" not in env:
            env["DISPLAY"] = ":0"

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

    return send_notification(
        title=title,
        message=message,
        icon=icon,
        urgency="normal",
        config=config,
    )
