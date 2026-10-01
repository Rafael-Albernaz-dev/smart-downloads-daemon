import time
import os
import threading
from unittest.mock import patch
from pathlib import Path
from smart_downloads_daemon.config import DaemonConfig
from smart_downloads_daemon.watcher import Watcher

def test_watcher_schedule_file(tmp_path: Path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()

    cfg = DaemonConfig(downloads_dir=downloads, grace_period_seconds=10)
    watcher = Watcher(cfg)

    # File that doesn't exist should not be scheduled
    watcher.schedule_file("nonexistent.pdf")
    assert "nonexistent.pdf" not in watcher.pending_files

    # File that exists should be scheduled
    test_file = downloads / "valid.pdf"
    test_file.write_text("dummy")

    watcher.schedule_file("valid.pdf", delay_seconds=5)
    assert "valid.pdf" in watcher.pending_files
    assert watcher.pending_files["valid.pdf"] > time.time()


def test_existing_file_retries_after_destination_returns(tmp_path: Path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    source = downloads / "old.pdf"
    source.write_text("preserved content")
    os.utime(source, (time.time() - 600, time.time() - 600))
    cfg = DaemonConfig(downloads_dir=downloads, destination_dir=tmp_path / "Docs")
    watcher = Watcher(cfg)

    with patch.object(DaemonConfig, "is_destination_available", return_value=False):
        before = time.time()
        watcher.process_existing_files()
    assert source.read_text() == "preserved content"
    assert before + 30 <= watcher.pending_files["old.pdf"] <= time.time() + 30

    stop = threading.Event()
    def disk_returns(timeout):
        watcher.pending_files["old.pdf"] = 0
        stop.set()
        return []
    with patch("smart_downloads_daemon.watcher.select.poll") as poll:
        poll.return_value.poll.side_effect = disk_returns
        watcher.run(stop)
    assert (cfg.destination_dir / "PDF" / "old.pdf").read_text() == "preserved content"
    assert not source.exists()
    assert not watcher.pending_files


def test_existing_file_move_failure_is_queued(tmp_path: Path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    source = downloads / "old.pdf"
    source.write_text("preserved content")
    os.utime(source, (time.time() - 600, time.time() - 600))
    watcher = Watcher(DaemonConfig(downloads_dir=downloads, destination_dir=tmp_path / "Docs"))
    with patch("smart_downloads_daemon.watcher.move_file", return_value=None):
        watcher.process_existing_files()
    assert "old.pdf" in watcher.pending_files
    assert source.read_text() == "preserved content"


def test_event_loop_move_failure_remains_queued(tmp_path: Path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    source = downloads / "old.pdf"
    source.write_text("preserved content")
    os.utime(source, (time.time() - 600, time.time() - 600))
    watcher = Watcher(DaemonConfig(downloads_dir=downloads, destination_dir=tmp_path / "Docs"))
    stop = threading.Event()
    def expire_file(timeout):
        watcher.pending_files["old.pdf"] = 0
        stop.set()
        return []
    with patch("smart_downloads_daemon.watcher.move_file", return_value=None), patch(
        "smart_downloads_daemon.watcher.select.poll"
    ) as poll:
        poll.return_value.poll.side_effect = expire_file
        watcher.run(stop)
    assert source.read_text() == "preserved content"
    assert watcher.pending_files["old.pdf"] > time.time()
