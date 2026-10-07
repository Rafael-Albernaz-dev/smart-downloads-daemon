from pathlib import Path
from smart_downloads_daemon.config import DaemonConfig
from smart_downloads_daemon.organizer import Organizer


def test_organizer_plan_creation(tmp_path: Path):
    dest = tmp_path / "Docs"
    pdf_dir = dest / "PDF"
    pdf_dir.mkdir(parents=True)

    # Create root files in PDF
    (pdf_dir / "fatura_luz.pdf").write_text("boleto")
    (pdf_dir / "drs_executivo.pdf").write_text("drs")

    # A file already in a subfolder should not be part of the plan
    boletos_dir = pdf_dir / "Boletos"
    boletos_dir.mkdir()
    (boletos_dir / "fatura_antiga.pdf").write_text("already organized")

    cfg = DaemonConfig(destination_dir=dest)
    organizer = Organizer(cfg)
    plan = organizer.create_plan()

    assert plan.total_files == 2
    assert "PDF" in plan.category_summary
    assert plan.category_summary["PDF"]["subcategories"]["Boletos"] == 1
    assert plan.category_summary["PDF"]["subcategories"]["DRS"] == 1


def test_organizer_execution_and_collision(tmp_path: Path):
    dest = tmp_path / "Docs"
    pdf_dir = dest / "PDF"
    pdf_dir.mkdir(parents=True)

    source_file = pdf_dir / "fatura_luz.pdf"
    source_file.write_text("new boleto")

    cfg = DaemonConfig(destination_dir=dest)
    organizer = Organizer(cfg)
    plan = organizer.create_plan()

    # 1. Test Dry Run
    dry_res = organizer.execute_plan(plan, dry_run=True)
    assert dry_res.dry_run is True
    assert source_file.exists()  # Not moved

    # 2. Pre-create target file to force collision resolution
    target_dir = pdf_dir / "Boletos"
    target_dir.mkdir()
    (target_dir / "fatura_luz.pdf").write_text("existing boleto")

    # 3. Test Real Execution
    real_res = organizer.execute_plan(plan, dry_run=False)
    assert real_res.files_moved == 1
    assert real_res.collisions_resolved == 1
    assert not source_file.exists()

    # Existing file was preserved
    assert (target_dir / "fatura_luz.pdf").read_text() == "existing boleto"

    # New file was renamed with timestamp
    collision_files = list(target_dir.glob("fatura_luz_*.pdf"))
    assert len(collision_files) == 1
    assert collision_files[0].read_text() == "new boleto"
