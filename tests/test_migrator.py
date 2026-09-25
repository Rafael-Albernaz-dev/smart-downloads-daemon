import os
from pathlib import Path
from smart_downloads_daemon.config import DaemonConfig
from smart_downloads_daemon.migrator import Migrator, format_bytes

def test_format_bytes():
    assert format_bytes(500) == "500 B"
    assert "KB" in format_bytes(2048)
    assert "MB" in format_bytes(1024 * 1024 * 5)
    assert "GB" in format_bytes(1024 * 1024 * 1024 * 10)

def test_migrator_plan_creation(tmp_path: Path):
    source = tmp_path / "SourceDocs"
    target = tmp_path / "TargetDrive"
    source.mkdir()
    target.mkdir()

    # Create category directories with dummy files
    pdf_dir = source / "PDF"
    pdf_dir.mkdir()
    (pdf_dir / "doc1.pdf").write_text("content1")
    (pdf_dir / "doc2.pdf").write_text("content22")

    img_dir = source / "Imagens"
    img_dir.mkdir()
    (img_dir / "photo.png").write_text("photo content")

    # Personal directory that should NOT be in plan
    personal_dir = source / "MyProjectRepo"
    personal_dir.mkdir()
    (personal_dir / "code.py").write_text("print('hello')")

    cfg = DaemonConfig(destination_dir=source)
    migrator = Migrator(cfg)

    plan = migrator.create_plan(from_dir=source, to_dir=target)
    assert plan.total_files == 3
    assert set(plan.categories_found) == {"PDF", "Imagens"}
    assert "MyProjectRepo" not in plan.categories_found

def test_migrator_execution_and_collision(tmp_path: Path):
    source = tmp_path / "SourceDocs"
    target = tmp_path / "TargetDrive"
    source.mkdir()
    target.mkdir()

    pdf_dir = source / "PDF"
    pdf_dir.mkdir()
    (pdf_dir / "test.pdf").write_text("new pdf")

    # Pre-create target collision
    target_pdf = target / "PDF"
    target_pdf.mkdir()
    (target_pdf / "test.pdf").write_text("existing pdf")

    cfg = DaemonConfig(destination_dir=source)
    migrator = Migrator(cfg)
    plan = migrator.create_plan(from_dir=source, to_dir=target)

    # 1. Test Dry Run
    dry_res = migrator.execute_plan(plan, dry_run=True)
    assert dry_res.dry_run is True
    assert (pdf_dir / "test.pdf").exists()  # Not moved

    # 2. Test Real Migration
    real_res = migrator.execute_plan(plan, dry_run=False)
    assert real_res.files_moved == 1
    assert real_res.collisions_resolved == 1
    assert not (pdf_dir / "test.pdf").exists()  # Moved
    assert not pdf_dir.exists()  # Empty category folder removed

    # Original target file was untouched
    assert (target_pdf / "test.pdf").read_text() == "existing pdf"

    # Collision file exists with new pdf content
    migrated_files = list(target_pdf.glob("test_*.pdf"))
    assert len(migrated_files) == 1
    assert migrated_files[0].read_text() == "new pdf"
