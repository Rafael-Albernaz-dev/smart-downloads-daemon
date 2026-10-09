from pathlib import Path
from unittest.mock import MagicMock, patch

from smart_downloads_daemon.config import DaemonConfig
from smart_downloads_daemon.notifier import (
    _detect_display_env,
    notify_organized,
    send_notification,
)


def test_send_notification_disabled_in_config():
    cfg = DaemonConfig(notifications_enabled=False)
    with patch("shutil.which") as mock_which:
        assert send_notification("Title", "Message", config=cfg) is False
        mock_which.assert_not_called()


def test_send_notification_notify_send_missing():
    cfg = DaemonConfig(notifications_enabled=True)
    with patch("shutil.which", return_value=None):
        assert send_notification("Title", "Message", config=cfg) is False


def test_send_notification_success():
    cfg = DaemonConfig(notifications_enabled=True)
    with patch("shutil.which", return_value="/usr/bin/notify-send"), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)

        result = send_notification(
            title="Smart Downloads",
            message="Organized file",
            icon="document-save",
            urgency="normal",
            config=cfg,
        )
        assert result is True
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/usr/bin/notify-send"
        assert "Smart Downloads" in cmd
        assert "Organized file" in cmd


def test_send_notification_subprocess_failure():
    cfg = DaemonConfig(notifications_enabled=True)
    with patch("shutil.which", return_value="/usr/bin/notify-send"), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        assert send_notification("Title", "Message", config=cfg) is False


def test_send_notification_subprocess_exception_handled():
    cfg = DaemonConfig(notifications_enabled=True)
    with patch("shutil.which", return_value="/usr/bin/notify-send"), \
         patch("subprocess.run", side_effect=Exception("D-Bus connection error")):
        assert send_notification("Title", "Message", config=cfg) is False


def test_detect_display_env_fallback():
    with patch.dict("os.environ", {}, clear=True):
        env = _detect_display_env()
        assert "DISPLAY" in env
        assert env["DISPLAY"].startswith(":")


def test_notify_organized_for_file_and_directory(tmp_path: Path):
    cfg = DaemonConfig(destination_dir=tmp_path / "Docs")

    with patch("smart_downloads_daemon.notifier.send_notification") as mock_send:
        mock_send.return_value = True

        # File notification
        dest_file = tmp_path / "Docs" / "PDF" / "doc.pdf"
        assert notify_organized("doc.pdf", dest_file, is_dir=False, config=cfg) is True
        mock_send.assert_called_with(
            title="Smart Downloads",
            message="Arquivo organizado:\ndoc.pdf ➔ PDF/doc.pdf",
            icon="document-save",
            urgency="normal",
            config=cfg,
        )

        # Directory notification
        dest_dir = tmp_path / "Docs" / "Pastas" / "minha_pasta"
        assert notify_organized("minha_pasta", dest_dir, is_dir=True, config=cfg) is True
        mock_send.assert_called_with(
            title="Smart Downloads",
            message="Pasta organizado:\nminha_pasta ➔ Pastas/minha_pasta",
            icon="folder",
            urgency="normal",
            config=cfg,
        )
