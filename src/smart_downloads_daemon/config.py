"""
Configuration loader and category mapping definitions.
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

DEFAULT_CATEGORIES: Dict[str, List[str]] = {
    "PDF": ["pdf"],
    "Textos": ["txt", "md", "doc", "docx", "odt", "rtf", "log"],
    "Planilhas": ["xls", "xlsx", "csv", "ods", "tsv"],
    "Imagens": ["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico", "tiff"],
    "Videos": ["mp4", "mkv", "avi", "mov", "webm", "flv", "wmv", "m4v"],
    "Audios": ["mp3", "wav", "ogg", "flac", "m4a", "aac", "wma"],
    "Compactados": ["zip", "tar", "gz", "bz2", "7z", "rar", "xz", "iso"],
}

DEFAULT_IGNORE_EXTENSIONS: Set[str] = {".crdownload", ".part", ".tmp", ".download"}
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "smart-downloads-daemon" / "config.json"

@dataclass
class DaemonConfig:
    downloads_dir: Path = field(default_factory=lambda: Path.home() / "Downloads")
    destination_dir: Path = field(default_factory=lambda: Path.home() / "Documents")
    grace_period_seconds: int = 300
    categories: Dict[str, List[str]] = field(default_factory=lambda: dict(DEFAULT_CATEGORIES))
    ignore_extensions: Set[str] = field(default_factory=lambda: set(DEFAULT_IGNORE_EXTENSIONS))

    @classmethod
    def load(cls, config_path: Optional[Path] = None) -> "DaemonConfig":
        path = config_path or DEFAULT_CONFIG_PATH
        cfg = cls()
        if path.is_file():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if "downloads_dir" in data:
                    cfg.downloads_dir = Path(os.path.expanduser(data["downloads_dir"]))
                if "destination_dir" in data:
                    cfg.destination_dir = Path(os.path.expanduser(data["destination_dir"]))
                if "grace_period_seconds" in data:
                    cfg.grace_period_seconds = int(data["grace_period_seconds"])
                if "categories" in data and isinstance(data["categories"], dict):
                    cfg.categories = data["categories"]
                if "ignore_extensions" in data and isinstance(data["ignore_extensions"], list):
                    cfg.ignore_extensions = set(data["ignore_extensions"])
            except Exception as e:
                print(f"[WARNING] Could not parse config from {path}: {e}")
        return cfg
