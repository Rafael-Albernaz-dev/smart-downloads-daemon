import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

from smart_downloads_daemon.cli import (
    acquire_instance_lock,
    format_duration,
    run_mount_cli,
    run_pause_cli,
    run_resume_cli,
    show_status,
)
from smart_downloads_daemon.config import DaemonConfig


def test_format_duration():
    assert format_duration(30) == "30s"
    assert format_duration(65) == "1m 05s"
    assert format_duration(120) == "2m 00s"
    assert format_duration(3665) == "1h 01m"


def test_acquire_instance_lock(tmp_path: Path):
    lock_file = tmp_path / "test.lock"
    f1 = acquire_instance_lock(lock_file)
    assert f1 is not None

    # Second acquisition on same lock should fail
    f2 = acquire_instance_lock(lock_file)
    assert f2 is None

    # Closing f1 allows acquisition again
    f1.close()
    f3 = acquire_instance_lock(lock_file)
    assert f3 is not None
    f3.close()


def test_pause_and_resume_cli(tmp_path: Path):
    cfg = DaemonConfig(config_dir=tmp_path)
    res_pause = run_pause_cli(cfg, reason="Testing")
    assert res_pause == 0
    assert cfg.is_paused() is True
    assert cfg.get_pause_info()["reason"] == "Testing"

    res_resume = run_resume_cli(cfg)
    assert res_resume == 0
    assert cfg.is_paused() is False


def test_show_status_json(tmp_path: Path):
    dl = tmp_path / "Downloads"
    docs = tmp_path / "Docs"
    dl.mkdir()
    docs.mkdir()

    cfg = DaemonConfig(downloads_dir=dl, destination_dir=docs, config_dir=tmp_path)
    (dl / "doc.pdf").write_text("sample")
    (dl / "temp.crdownload").write_text("in flight")

    captured_out = io.StringIO()
    with patch("sys.stdout", captured_out), patch(
        "smart_downloads_daemon.cli.get_service_info",
        return_value={"active": True, "pid": 1234, "uptime_seconds": 60, "status_str": "active"},
    ):
        show_status(cfg, as_json=True)

    output = captured_out.getvalue()
    data = json.loads(output)
    assert data["service"]["active"] is True
    assert data["service"]["pid"] == 1234
    assert data["paused"] is False
    assert len(data["queue"]["pending_files"]) == 1
    assert data["queue"]["pending_files"][0]["name"] == "doc.pdf"
    assert "temp.crdownload" in data["queue"]["ignored_files"]


def test_run_mount_cli_when_already_available(tmp_path: Path):
    dest = tmp_path / "Docs"
    dest.mkdir()
    cfg = DaemonConfig(destination_dir=dest, config_dir=tmp_path)
    ret = run_mount_cli(cfg)
    assert ret == 0
