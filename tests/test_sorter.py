from pathlib import Path
from smart_downloads_daemon.config import DaemonConfig
from smart_downloads_daemon.sorter import get_destination_folder, is_valid_file, move_file

def test_is_valid_file():
    cfg = DaemonConfig()
    # Ignored files
    assert is_valid_file(".hidden_file", cfg) is False
    assert is_valid_file("document.pdf.crdownload", cfg) is False
    assert is_valid_file("archive.zip.part", cfg) is False
    assert is_valid_file("tempfile.tmp", cfg) is False
    assert is_valid_file("document.txt~", cfg) is False
    assert is_valid_file("#autosave#", cfg) is False

    # Valid files
    assert is_valid_file("invoice.pdf", cfg) is True
    assert is_valid_file("photo.JPG", cfg) is True
    assert is_valid_file("archive.tar.gz", cfg) is True

def test_get_destination_folder(tmp_path: Path):
    cfg = DaemonConfig(destination_dir=tmp_path / "Docs")

    assert get_destination_folder("paper.pdf", cfg) == tmp_path / "Docs" / "PDF"
    assert get_destination_folder("photo.png", cfg) == tmp_path / "Docs" / "Imagens"
    assert get_destination_folder("clip.mp4", cfg) == tmp_path / "Docs" / "Videos"
    assert get_destination_folder("notes.txt", cfg) == tmp_path / "Docs" / "Textos"
    assert get_destination_folder("data.csv", cfg) == tmp_path / "Docs" / "Planilhas"
    assert get_destination_folder("package.zip", cfg) == tmp_path / "Docs" / "Compactados"
    assert get_destination_folder("unknown.xyz123", cfg) == tmp_path / "Docs" / "Outros"
    assert get_destination_folder("noextension", cfg) == tmp_path / "Docs" / "Outros"

def test_move_file_with_collision_resolution(tmp_path: Path):
    downloads = tmp_path / "Downloads"
    docs = tmp_path / "Docs"
    downloads.mkdir()
    docs.mkdir()

    cfg = DaemonConfig(downloads_dir=downloads, destination_dir=docs)

    # 1. Create source file
    source_file = downloads / "contract.pdf"
    source_file.write_text("v1 content")

    # 2. Pre-create existing file in destination to force collision
    pdf_dest = docs / "PDF"
    pdf_dest.mkdir()
    existing_dest = pdf_dest / "contract.pdf"
    existing_dest.write_text("existing content")

    # 3. Move file
    moved_path = move_file("contract.pdf", cfg)
    assert moved_path is not None
    assert moved_path.exists()
    assert moved_path.name != "contract.pdf"
    assert moved_path.name.startswith("contract_")
    assert moved_path.suffix == ".pdf"

    # Original destination file was untouched
    assert existing_dest.read_text() == "existing content"
    # Moved file contains source content
    assert moved_path.read_text() == "v1 content"
