"""
POSIX libc inotify ctypes wrapper and binary struct parsing.
"""

import ctypes
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

# Linux kernel inotify event flags
IN_CLOSE_WRITE = 0x00000008  # File opened for writing was closed
IN_CLOSE_NOWRITE = 0x00000010
IN_OPEN        = 0x00000020
IN_MOVED_TO    = 0x00000080  # File moved/renamed into monitored directory
IN_CREATE      = 0x00000100  # Subfile or directory created in monitored directory
IN_DELETE      = 0x00000200  # Subfile or directory deleted from monitored directory
IN_MOVED_FROM  = 0x00000040  # File moved out of monitored directory
IN_ISDIR       = 0x40000000  # Event occurred against directory
IN_DELETE_SELF = 0x00000400
IN_MOVE_SELF   = 0x00000800
IN_UNMOUNT     = 0x00002000
IN_Q_OVERFLOW  = 0x00004000
IN_IGNORED     = 0x00008000

DEFAULT_MASK   = (IN_OPEN | IN_CLOSE_WRITE | IN_CLOSE_NOWRITE | IN_MOVED_TO | IN_CREATE | IN_DELETE |
                  IN_MOVED_FROM | IN_DELETE_SELF | IN_MOVE_SELF)

EVENT_FMT = "iIII"
EVENT_SIZE = struct.calcsize(EVENT_FMT)

@dataclass(frozen=True)
class InotifyEvent:
    wd: int
    mask: int
    cookie: int
    name: str

class Inotify:
    """Low-level ctypes binding to Linux inotify syscalls."""

    def __init__(self, watch_dir: Path, mask: int = DEFAULT_MASK):
        self.watch_dir = watch_dir
        self.mask = mask
        self._libc = ctypes.CDLL(None, use_errno=True)
        self._libc.inotify_init1.argtypes = [ctypes.c_int]
        self._libc.inotify_init1.restype = ctypes.c_int
        self._libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        self._libc.inotify_add_watch.restype = ctypes.c_int
        
        self.fd = self._libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if self.fd < 0:
            errno = ctypes.get_errno()
            raise OSError(errno, os.strerror(errno))

        self.wd = self._libc.inotify_add_watch(self.fd, os.fsencode(watch_dir), self.mask)
        if self.wd < 0:
            errno = ctypes.get_errno()
            os.close(self.fd)
            raise OSError(errno, os.strerror(errno), str(watch_dir))

    def read_events(self) -> Iterator[InotifyEvent]:
        """Read and unpack inotify binary events from file descriptor."""
        if self.fd < 0:
            return
        # Bound work per poll iteration so a constant event stream cannot
        # starve grace-period deadlines.
        for _ in range(16):
            try:
                data = os.read(self.fd, 4096)
            except BlockingIOError:
                return
            if not data:
                raise OSError("inotify descriptor closed")

            offset = 0
            while offset + EVENT_SIZE <= len(data):
                wd, mask, cookie, length = struct.unpack_from(EVENT_FMT, data, offset)
                offset += EVENT_SIZE
                if offset + length > len(data):
                    raise OSError("truncated inotify event")
                name_bytes = data[offset : offset + length]
                offset += length
                name = os.fsdecode(name_bytes.split(b"\x00", 1)[0])
                yield InotifyEvent(wd, mask, cookie, name)

    def close(self) -> None:
        """Close inotify file descriptor."""
        if self.fd >= 0:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = -1
