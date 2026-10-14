import errno
from pathlib import Path
from unittest.mock import patch

import pytest

from smart_downloads_daemon.sorter import sanitize_filename
from smart_downloads_daemon.transfer import _remove_copied_tree, rename_noreplace, verified_move


def cross_device_for(source: Path):
    def rename(src: Path, dst: Path) -> None:
        if src == source:
            raise OSError(errno.EXDEV, "simulated cross-device transfer")
        rename_noreplace(src, dst)
    return rename


def test_cross_device_file_published_after_verification(tmp_path: Path):
    source = tmp_path / "source.txt"
    source.write_bytes(b"important data")
    destination = tmp_path / "target" / "source.txt"

    with patch("smart_downloads_daemon.transfer.rename_noreplace", side_effect=cross_device_for(source)):
        assert verified_move(source, destination) == destination

    assert destination.read_bytes() == b"important data"
    assert not source.exists()
    assert not list(destination.parent.glob(".smartdown-*"))


def test_same_size_corruption_keeps_source_and_hides_destination(tmp_path: Path):
    source = tmp_path / "source.txt"
    source.write_bytes(b"good")
    destination = tmp_path / "target" / "source.txt"

    def corrupt_copy(_src, dst):
        Path(dst).write_bytes(b"evil")

    with patch("smart_downloads_daemon.transfer.rename_noreplace", side_effect=cross_device_for(source)), patch(
        "smart_downloads_daemon.transfer.shutil.copy2", side_effect=corrupt_copy
    ):
        with pytest.raises(OSError):
            verified_move(source, destination)

    assert source.read_bytes() == b"good"
    assert not destination.exists()
    assert not list(destination.parent.glob(".smartdown-*"))


def test_cross_device_directory_sanitizes_nested_names(tmp_path: Path):
    source = tmp_path / "folder"
    source.mkdir()
    (source / "CON.txt").write_text("one")
    (source / "a:b.txt").write_text("two")
    (source / "a?b.txt").write_text("three")
    destination = tmp_path / "target" / "folder"

    with patch("smart_downloads_daemon.transfer.rename_noreplace", side_effect=cross_device_for(source)):
        verified_move(source, destination, sanitize_name=sanitize_filename)

    assert not source.exists()
    assert (destination / "_CON.txt").read_text() == "one"
    assert (destination / "a_b.txt").read_text() == "two"
    assert (destination / "a_b_1.txt").read_text() == "three"


def test_existing_destination_is_never_replaced(tmp_path: Path):
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("source")
    destination.write_text("existing")
    with pytest.raises(FileExistsError):
        verified_move(source, destination)
    assert source.read_text() == "source"
    assert destination.read_text() == "existing"


def test_new_directory_entry_is_retained_during_cleanup(tmp_path: Path):
    source = tmp_path / "folder"
    source.mkdir()
    (source / "original.txt").write_text("original")
    destination = tmp_path / "target" / "folder"

    def add_new_file_before_cleanup(path, manifest):
        (path / "new.txt").write_text("new")
        _remove_copied_tree(path, manifest)

    with patch("smart_downloads_daemon.transfer.rename_noreplace", side_effect=cross_device_for(source)), patch(
        "smart_downloads_daemon.transfer._remove_copied_tree", side_effect=add_new_file_before_cleanup
    ):
        verified_move(source, destination)

    assert (destination / "original.txt").read_text() == "original"
    assert (source / "new.txt").read_text() == "new"
    assert not (source / "original.txt").exists()
