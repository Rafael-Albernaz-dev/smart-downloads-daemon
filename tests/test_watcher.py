import time
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
