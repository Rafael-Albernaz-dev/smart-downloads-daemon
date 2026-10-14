import os
import struct
from pathlib import Path
from unittest.mock import patch
from smart_downloads_daemon.inotify import EVENT_SIZE, IN_Q_OVERFLOW, Inotify

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
        IN_DELETE,
        IN_ISDIR,
        IN_MOVED_FROM,
        IN_MOVED_TO,
    )
    assert IN_CLOSE_WRITE == 0x00000008
    assert IN_MOVED_TO == 0x00000080
    assert IN_CREATE == 0x00000100
    assert IN_DELETE == 0x00000200
    assert IN_MOVED_FROM == 0x00000040
    assert IN_ISDIR == 0x40000000

    assert (DEFAULT_MASK & IN_CLOSE_WRITE) != 0
    assert (DEFAULT_MASK & IN_MOVED_TO) != 0
    assert (DEFAULT_MASK & IN_CREATE) != 0
    assert (DEFAULT_MASK & IN_DELETE) != 0
    assert (DEFAULT_MASK & IN_MOVED_FROM) != 0


def test_nameless_overflow_event_is_preserved():
    watcher = Inotify.__new__(Inotify)
    watcher.fd = 42
    packet = struct.pack("iIII", -1, IN_Q_OVERFLOW, 0, 0)
    with patch("os.read", side_effect=[packet, BlockingIOError()]):
        events = list(watcher.read_events())
    assert len(events) == 1
    assert events[0].mask == IN_Q_OVERFLOW
    assert events[0].name == ""

