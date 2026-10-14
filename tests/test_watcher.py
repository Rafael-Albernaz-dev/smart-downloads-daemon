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


def test_watcher_paused_does_not_move_files(tmp_path: Path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    docs = tmp_path / "Docs"
    source = downloads / "hold.pdf"
    source.write_text("hold this")
    os.utime(source, (time.time() - 600, time.time() - 600))

    cfg = DaemonConfig(downloads_dir=downloads, destination_dir=docs, config_dir=tmp_path)
    cfg.set_paused(True)
    watcher = Watcher(cfg)

    # In paused mode, existing expired files are scheduled with 0 delay but not moved
    watcher.process_existing_files()
    assert source.exists()
    assert "hold.pdf" in watcher.pending_files

    stop = threading.Event()
    def one_iter(timeout):
        stop.set()
        return []

    with patch("smart_downloads_daemon.watcher.select.poll") as poll:
        poll.return_value.poll.side_effect = one_iter
        watcher.run(stop)

    # File must still be in Downloads because daemon is paused
    assert source.exists()
    assert not (docs / "PDF" / "hold.pdf").exists()

    # Now unpause and run another iteration
    cfg.set_paused(False)
    stop2 = threading.Event()
    def second_iter(timeout):
        stop2.set()
        return []

    with patch("smart_downloads_daemon.watcher.select.poll") as poll:
        poll.return_value.poll.side_effect = second_iter
        watcher.run(stop2)

    # Now file is moved!
    assert not source.exists()
    assert (docs / "PDF" / "hold.pdf").read_text() == "hold this"


def test_watcher_schedules_directory(tmp_path: Path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()

    cfg = DaemonConfig(downloads_dir=downloads, grace_period_seconds=10)
    watcher = Watcher(cfg)

    # Directory that exists should be scheduled
    test_dir = downloads / "pacote_fotos"
    test_dir.mkdir()

    watcher.schedule_file("pacote_fotos", delay_seconds=5)
    assert "pacote_fotos" in watcher.pending_files
    assert watcher.pending_files["pacote_fotos"] > time.time()


def test_watcher_directory_in_flight_not_scheduled(tmp_path: Path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()

    cfg = DaemonConfig(downloads_dir=downloads, grace_period_seconds=10)
    watcher = Watcher(cfg)

    active_dir = downloads / "torrent_download"
    active_dir.mkdir()
    (active_dir / "filme.mp4.part").write_text("incompleto")

    watcher.schedule_file("torrent_download", delay_seconds=5)
    # Retry even without another root-level event when the nested .part is removed.
    assert "torrent_download" in watcher.pending_files


def test_nested_download_retried_after_completion(tmp_path: Path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    folder = downloads / "bundle"
    folder.mkdir()
    part = folder / "video.part"
    part.write_text("partial")
    cfg = DaemonConfig(downloads_dir=downloads, destination_dir=tmp_path / "Docs", config_dir=tmp_path)
    watcher = Watcher(cfg)
    watcher.schedule_file("bundle", notify=False)
    assert "bundle" in watcher.pending_files
    part.rename(folder / "video.mp4")

    stop = threading.Event()
    def one_iteration(_timeout):
        watcher.pending_files["bundle"] = 0
        stop.set()
        return []

    with patch("smart_downloads_daemon.watcher.select.poll") as poll:
        poll.return_value.poll.side_effect = one_iteration
        watcher.run(stop)
    assert (cfg.destination_dir / "Pastas" / "bundle" / "video.mp4").read_text() == "partial"


def test_poll_loop_remains_responsive_during_transfer(tmp_path: Path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "old.txt").write_text("data")
    cfg = DaemonConfig(downloads_dir=downloads, destination_dir=tmp_path / "Docs",
                       config_dir=tmp_path, grace_period_seconds=0)
    watcher = Watcher(cfg)
    stop = threading.Event()
    transfer_started = threading.Event()
    release_transfer = threading.Event()
    poll_calls = []

    def slow_move(_name, _config):
        transfer_started.set()
        release_transfer.wait(2)
        return None

    def poll_once_more(_timeout):
        poll_calls.append(1)
        if len(poll_calls) == 2:
            assert transfer_started.wait(1)
            release_transfer.set()
            stop.set()
        return []

    with patch("smart_downloads_daemon.watcher.move_file", side_effect=slow_move), patch(
        "smart_downloads_daemon.watcher.select.poll"
    ) as poll:
        poll.return_value.poll.side_effect = poll_once_more
        watcher.run(stop)
    assert len(poll_calls) >= 2


def test_process_existing_directories(tmp_path: Path):
    downloads = tmp_path / "Downloads"
    docs = tmp_path / "Docs"
    downloads.mkdir()
    docs.mkdir()

    # Pre-existing old directory (older than grace period)
    old_dir = downloads / "pastinha_antiga"
    old_dir.mkdir()
    (old_dir / "info.txt").write_text("dados")
    past_time = time.time() - 600
    os.utime(old_dir, (past_time, past_time))
    os.utime(old_dir / "info.txt", (past_time, past_time))

    cfg = DaemonConfig(downloads_dir=downloads, destination_dir=docs, grace_period_seconds=300)
    watcher = Watcher(cfg)

    watcher.process_existing_files()
    # Should have been organized directly into Docs/Pastas
    assert not old_dir.exists()
    assert (docs / "Pastas" / "pastinha_antiga" / "info.txt").read_text() == "dados"


def test_watcher_removes_deleted_file_from_queue(tmp_path: Path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()

    cfg = DaemonConfig(downloads_dir=downloads, grace_period_seconds=300)
    watcher = Watcher(cfg)

    file_path = downloads / "temporario.pdf"
    file_path.write_text("conteudo")

    watcher.schedule_file("temporario.pdf", delay_seconds=100)
    assert "temporario.pdf" in watcher.pending_files

    # File is deleted from disk
    file_path.unlink()

    # When inotify triggers on delete/move_from, schedule_file cleans up the pending queue
    watcher.schedule_file("temporario.pdf")
    assert "temporario.pdf" not in watcher.pending_files


