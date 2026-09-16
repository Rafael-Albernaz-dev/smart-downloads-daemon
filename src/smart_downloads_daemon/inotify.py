"""
POSIX libc inotify ctypes wrapper and binary struct parsing.
"""

import ctypes
import os
import struct
from pathlib import Path
from typing import Iterator

# Linux kernel inotify event flags
IN_CLOSE_WRITE = 0x00000008  # File opened for writing was closed
IN_MOVED_TO    = 0x00000080  # File moved/renamed into monitored directory

EVENT_FMT = "iIII"
EVENT_SIZE = struct.calcsize(EVENT_FMT)

class Inotify:
    """Low-level ctypes binding to Linux inotify syscalls."""

    def __init__(self, watch_dir: Path, mask: int = IN_CLOSE_WRITE | IN_MOVED_TO):
        self.watch_dir = watch_dir
        self.mask = mask
        self._libc = ctypes.CDLL(None)
        
        self.fd = self._libc.inotify_init1(0)
        if self.fd < 0:
            raise OSError("Failed to initialize Linux inotify")

        self.wd = self._libc.inotify_add_watch(self.fd, str(watch_dir).encode(), self.mask)
        if self.wd < 0:
            os.close(self.fd)
            raise OSError(f"Failed to add inotify watch on {watch_dir}")

    def read_events(self) -> Iterator[str]:
        """Read and unpack inotify binary events from file descriptor."""
        data = os.read(self.fd, 4096)
        offset = 0
        while offset + EVENT_SIZE <= len(data):
            _, _, _, length = struct.unpack_from(EVENT_FMT, data, offset)
            offset += EVENT_SIZE
            name_bytes = data[offset : offset + length]
            offset += length

            name = name_bytes.rstrip(b"\x00").decode("utf-8", errors="ignore")
            if name:
                yield name

    def close(self) -> None:
        """Close inotify file descriptor."""
        if self.fd >= 0:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = -1
