import os
from pathlib import Path
from smart_downloads_daemon.inotify import EVENT_SIZE, Inotify

def test_inotify_struct_size():
    assert EVENT_SIZE == 16

def test_inotify_initialization(tmp_path: Path):
    watch_dir = tmp_path / "watch"
    watch_dir.mkdir()

    inotify = Inotify(watch_dir)
    assert inotify.fd >= 0
    assert inotify.wd >= 0

    inotify.close()
    assert inotify.fd == -1
