import json
from pathlib import Path
from unittest.mock import patch

from smart_downloads_daemon.config import DaemonConfig
from smart_downloads_daemon.history import (
    clear_history,
    format_log_entry,
    load_history,
    record_event,
)
from smart_downloads_daemon.cli import run_logs_cli
from smart_downloads_daemon.sorter import move_file


def test_record_and_load_history(tmp_path: Path):
    cfg = DaemonConfig(config_dir=tmp_path, destination_dir=tmp_path / "Docs")

    e1 = record_event(
        config=cfg,
        action="move",
        source="doc1.pdf",
        destination=tmp_path / "Docs" / "PDF" / "Boletos" / "doc1.pdf",
        category="PDF",
        subcategory="Boletos",
        size_bytes=1024,
        collision=False,
        timestamp="2026-10-07 12:00:00",
    )
    assert e1["action"] == "move"
    assert e1["category"] == "PDF"
    assert e1["subcategory"] == "Boletos"

    e2 = record_event(
        config=cfg,
        action="organize",
        source="planilha.xlsx",
        destination=tmp_path / "Docs" / "Planilhas" / "Cobranca" / "planilha.xlsx",
        category="Planilhas",
        subcategory="Cobranca",
        size_bytes=2048,
        collision=True,
        timestamp="2026-10-07 12:05:00",
    )
    assert e2["collision"] is True

    # Load history (default reverse=True, newest first)
    entries = load_history(cfg, limit=10, reverse=True)
    assert len(entries) == 2
    assert entries[0]["source"] == "planilha.xlsx"
    assert entries[1]["source"] == "doc1.pdf"

    # Test limit
    entries_lim = load_history(cfg, limit=1, reverse=True)
    assert len(entries_lim) == 1
    assert entries_lim[0]["source"] == "planilha.xlsx"


def test_history_filtering(tmp_path: Path):
    cfg = DaemonConfig(config_dir=tmp_path, destination_dir=tmp_path / "Docs")

    record_event(
        config=cfg,
        action="move",
        source="file_a.pdf",
        destination=tmp_path / "Docs" / "PDF" / "file_a.pdf",
        category="PDF",
        timestamp="2026-10-07 10:00:00",
    )
    record_event(
        config=cfg,
        action="migrate",
        source="file_b.csv",
        destination=tmp_path / "Docs" / "Planilhas" / "file_b.csv",
        category="Planilhas",
        timestamp="2026-10-07 10:05:00",
    )
    record_event(
        config=cfg,
        action="organize",
        source="file_c.pdf",
        destination=tmp_path / "Docs" / "PDF" / "file_c.pdf",
        category="PDF",
        timestamp="2026-10-07 10:10:00",
    )

    # Filter by action
    moves = load_history(cfg, action="move")
    assert len(moves) == 1
    assert moves[0]["source"] == "file_a.pdf"

    migrates = load_history(cfg, action="migrate")
    assert len(migrates) == 1
    assert migrates[0]["source"] == "file_b.csv"

    # Filter by category
    pdfs = load_history(cfg, category="PDF")
    assert len(pdfs) == 2
    assert {p["source"] for p in pdfs} == {"file_a.pdf", "file_c.pdf"}


def test_clear_history(tmp_path: Path):
    cfg = DaemonConfig(config_dir=tmp_path, destination_dir=tmp_path / "Docs")

    record_event(
        config=cfg,
        action="move",
        source="test.txt",
        destination=tmp_path / "Docs" / "Textos" / "test.txt",
    )
    assert len(load_history(cfg)) == 1

    assert clear_history(cfg) is True
    assert len(load_history(cfg)) == 0


def test_format_log_entry():
    entry = {
        "timestamp": "2026-10-07 19:00:00",
        "action": "move",
        "category": "PDF",
        "subcategory": "Boletos",
        "source": "boleto.pdf",
        "destination": "/media/disk/PDF/Boletos/boleto.pdf",
        "size_bytes": 1048576,
        "collision": True,
    }
    line = format_log_entry(entry)
    assert "2026-10-07 19:00:00" in line
    assert "[MOVE" in line
    assert "PDF/Boletos" in line
    assert "boleto.pdf" in line
    assert "1.0 MB" in line
    assert "[COLLISION]" in line


def test_run_logs_cli(tmp_path: Path, capsys):
    cfg = DaemonConfig(config_dir=tmp_path, destination_dir=tmp_path / "Docs")
    record_event(
        config=cfg,
        action="move",
        source="sample.pdf",
        destination=tmp_path / "Docs" / "PDF" / "sample.pdf",
        category="PDF",
        timestamp="2026-10-07 19:00:00",
    )

    # Normal display
    code = run_logs_cli(cfg, limit=10)
    assert code == 0
    captured = capsys.readouterr().out
    assert "SMARTDOWN - RECENT ACTIVITY LOGS" in captured
    assert "sample.pdf" in captured

    # JSON display
    code_json = run_logs_cli(cfg, as_json=True)
    assert code_json == 0
    captured_json = capsys.readouterr().out
    data = json.loads(captured_json)
    assert len(data) == 1
    assert data[0]["source"] == "sample.pdf"

    # Clear logs
    code_clear = run_logs_cli(cfg, clear=True)
    assert code_clear == 0
    assert len(load_history(cfg)) == 0


def test_move_file_records_history(tmp_path: Path):
    dl = tmp_path / "Downloads"
    docs = tmp_path / "Docs"
    dl.mkdir()
    docs.mkdir()

    cfg = DaemonConfig(downloads_dir=dl, destination_dir=docs, config_dir=tmp_path)
    (dl / "fatura_luz.pdf").write_text("boleto 123")

    moved = move_file("fatura_luz.pdf", cfg)
    assert moved is not None

    hist = load_history(cfg)
    assert len(hist) == 1
    assert hist[0]["source"] == "fatura_luz.pdf"
    assert hist[0]["action"] == "move"
    assert hist[0]["category"] == "PDF"
    assert hist[0]["subcategory"] == "Boletos"
