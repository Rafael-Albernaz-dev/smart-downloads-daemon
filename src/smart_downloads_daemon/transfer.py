"""Verified, no-replace file transfers for Linux filesystems."""

import ctypes
import errno
import hashlib
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Callable, Optional


def rename_noreplace(source: Path, destination: Path) -> None:
    """Publish a path atomically without replacing an existing path."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise OSError(errno.ENOSYS, "renameat2 is unavailable")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))


def _digest(path: Path) -> bytes:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.digest()


def _identity(path: Path) -> tuple:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _sync_file(path: Path) -> None:
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def _sync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as error:
        # Some removable filesystems do not support directory fsync.
        if error.errno not in (errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP):
            raise


def _copy_file_verified(source: Path, temporary: Path) -> None:
    before = _identity(source)
    shutil.copy2(source, temporary)
    _sync_file(temporary)
    if _digest(source) != _digest(temporary) or _identity(source) != before:
        raise OSError(errno.EAGAIN, "source changed or copy verification failed", str(source))


def _raise_walk_error(error: OSError) -> None:
    raise error


def _copy_tree_verified(source: Path, temporary: Path,
                        sanitize_name: Callable[[str], str]) -> dict:
    temporary.mkdir()
    before = {}
    target_paths = {source: temporary}
    for root, dirs, files in os.walk(source, followlinks=False, onerror=_raise_walk_error):
        target_root = target_paths[Path(root)]
        for name in list(dirs):
            child = Path(root) / name
            if child.is_symlink():
                raise OSError(errno.ELOOP, "symlink inside source directory", str(child))
            before[str(child.relative_to(source))] = _identity(child)
        # Map each level independently so sanitized names never collide.
        used = set()
        for name in dirs + files:
            candidate = sanitize_name(name)
            stem, suffix = os.path.splitext(candidate)
            index = 1
            while candidate.casefold() in used:
                candidate = f"{stem}_{index}{suffix}"
                index += 1
            used.add(candidate.casefold())
            child = Path(root) / name
            target = target_root / candidate
            if name in dirs:
                target.mkdir()
                # os.walk only gives source paths, so descendants use this map.
                target_paths[child] = target
            else:
                if child.is_symlink() or not child.is_file():
                    raise OSError(errno.EINVAL, "unsupported item inside directory", str(child))
                before[str(child.relative_to(source))] = _identity(child)
                _copy_file_verified(child, target)
        _sync_directory(target_root)
    after = {}
    for root, dirs, files in os.walk(source, followlinks=False, onerror=_raise_walk_error):
        for name in dirs + files:
            child = Path(root) / name
            after[str(child.relative_to(source))] = _identity(child)
    if before != after:
        raise OSError(errno.EAGAIN, "source directory changed during transfer", str(source))
    return before


def _remove_copied_tree(source: Path, manifest: dict) -> None:
    """Remove only copied entries; leave new or modified source entries intact."""
    files = []
    directories = []
    for relative, identity in manifest.items():
        path = source / relative
        if path.is_dir():
            directories.append(path)
        else:
            files.append((path, identity))
    for path, identity in files:
        if path.is_symlink() or _identity(path) != identity:
            continue
        path.unlink()
    for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        try:
            path.rmdir()
        except OSError as error:
            if error.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                raise
    try:
        source.rmdir()
    except OSError as error:
        if error.errno not in (errno.ENOTEMPTY, errno.EEXIST):
            raise
    if source.exists():
        raise OSError(errno.EAGAIN, "new or modified source entries were retained", str(source))


def verified_move(source: Path, destination: Path,
                  sanitize_name: Optional[Callable[[str], str]] = None) -> Path:
    """Move with no replacement; copy to a hidden staging path across devices."""
    if source.is_symlink():
        raise OSError(errno.ELOOP, "source symlinks are not moved", str(source))
    if not source.is_dir() and not source.is_file():
        raise OSError(errno.EINVAL, "only regular files and directories are moved", str(source))
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        rename_noreplace(source, destination)
        try:
            _sync_directory(destination.parent)
        except OSError as error:
            print(f"[WARNING] Directory sync failed after rename to {destination}: {error}", file=sys.stderr)
        return destination
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise

    temporary = destination.parent / f".smartdown-{uuid.uuid4().hex}.tmp"
    published = False
    source_identity = _identity(source)
    manifest = None
    try:
        if source.is_dir():
            if sanitize_name is None:
                sanitize_name = lambda name: name
            manifest = _copy_tree_verified(source, temporary, sanitize_name)
        else:
            _copy_file_verified(source, temporary)
        _sync_directory(temporary if temporary.is_dir() else temporary.parent)
        rename_noreplace(temporary, destination)
        published = True
        try:
            _sync_directory(destination.parent)
            if _identity(source) != source_identity:
                raise OSError(errno.EAGAIN, "source changed after copy", str(source))
            if source.is_dir():
                _remove_copied_tree(source, manifest)
            else:
                source.unlink()
            _sync_directory(source.parent)
        except OSError as error:
            # The verified destination is already published. Preserve it rather
            # than turn a cleanup problem into data loss.
            print(f"[WARNING] Source cleanup incomplete for {source}: {error}", file=sys.stderr)
        return destination
    finally:
        if not published and temporary.exists():
            if temporary.is_dir():
                shutil.rmtree(temporary)
            else:
                temporary.unlink()
