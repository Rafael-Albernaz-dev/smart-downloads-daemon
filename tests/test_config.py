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

def test_config_save_and_reload(tmp_path: Path):
    target_config = tmp_path / "config.json"
    cfg = DaemonConfig(
        downloads_dir=tmp_path / "MyDownloads",
        destination_dir=tmp_path / "ExternalDrive",
        grace_period_seconds=60,
    )

    saved_path = cfg.save(target_config)
    assert saved_path == target_config
    assert target_config.is_file()

    loaded_cfg = DaemonConfig.load(target_config)
    assert loaded_cfg.downloads_dir == tmp_path / "MyDownloads"
    assert loaded_cfg.destination_dir == tmp_path / "ExternalDrive"
    assert loaded_cfg.grace_period_seconds == 60

def test_path_availability(tmp_path: Path):
    from smart_downloads_daemon.config import is_path_available
    # Existing writable dir
    assert is_path_available(tmp_path) is True
    # Non-existent subfolder in writable dir
    assert is_path_available(tmp_path / "subfolder") is True
    # Simulated unmounted /media path
    fake_unmounted = Path("/media/toru/NONEXISTENT_DISK_9999/folder")
    assert is_path_available(fake_unmounted) is False

