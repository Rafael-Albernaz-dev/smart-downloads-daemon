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


def test_inotify_constants_and_default_mask():
    from smart_downloads_daemon.inotify import (
        DEFAULT_MASK,
        IN_CLOSE_WRITE,
        IN_CREATE,
        IN_ISDIR,
        IN_MOVED_TO,
    )
    assert IN_CLOSE_WRITE == 0x00000008
    assert IN_MOVED_TO == 0x00000080
    assert IN_CREATE == 0x00000100
    assert IN_ISDIR == 0x40000000

    assert (DEFAULT_MASK & IN_CLOSE_WRITE) != 0
    assert (DEFAULT_MASK & IN_MOVED_TO) != 0
    assert (DEFAULT_MASK & IN_CREATE) != 0

