import json
from pathlib import Path
from smart_downloads_daemon.config import DaemonConfig, DEFAULT_CATEGORIES

def test_default_config():
    cfg = DaemonConfig()
    assert cfg.grace_period_seconds == 300
    assert cfg.downloads_dir == Path.home() / "Downloads"
    assert cfg.destination_dir == Path.home() / "Documents"
    assert "PDF" in cfg.categories
    assert ".crdownload" in cfg.ignore_extensions

def test_custom_config_loading(tmp_path: Path):
    custom_json = tmp_path / "custom.json"
    custom_json.write_text(json.dumps({
        "grace_period_seconds": 120,
        "categories": {"Code": ["py", "rs", "go"]},
        "ignore_extensions": [".bak", ".swp"]
    }))

    cfg = DaemonConfig.load(custom_json)
    assert cfg.grace_period_seconds == 120
    assert cfg.categories == {"Code": ["py", "rs", "go"]}
    assert cfg.ignore_extensions == {".bak", ".swp"}
