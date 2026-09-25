"""
Configuration loader, storage discovery, and category mapping definitions.
"""

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

DEFAULT_CATEGORIES: Dict[str, List[str]] = {
    "PDF": ["pdf"],
    "Textos": ["txt", "md", "doc", "docx", "odt", "rtf", "log"],
    "Planilhas": ["xls", "xlsx", "csv", "ods", "tsv"],
    "Imagens": ["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico", "tiff"],
    "Videos": ["mp4", "mkv", "avi", "mov", "webm", "flv", "wmv", "m4v"],
    "Audios": ["mp3", "wav", "ogg", "flac", "m4a", "aac", "wma"],
    "Compactados": ["zip", "tar", "gz", "bz2", "7z", "rar", "xz", "iso"],
}

DEFAULT_IGNORE_EXTENSIONS: Set[str] = {
    ".crdownload",
    ".part",
    ".tmp",
    ".download",
    ".opdownload",
    ".aria2",
}
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "smart-downloads-daemon" / "config.json"

def is_path_available(path: Path) -> bool:
    """
    Check if a path or its target mount point is accessible and writable.
    Protects against writing to unmounted /media or /mnt paths.
    """
    try:
        p = path.expanduser().resolve()
    except Exception:
        return False

    if p.exists():
        return os.access(p, os.W_OK)

    # Protect against unmounted /media or /mnt devices
    parts = p.parts
    if len(parts) >= 3 and parts[1] in ("media", "mnt"):
        mount_root = Path(*parts[:4]) if len(parts) >= 4 else Path(*parts[:3])
        if not mount_root.exists():
            return False

    parent = p.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent

    return parent.exists() and os.access(parent, os.W_OK)

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

    def save(self, config_path: Optional[Path] = None) -> Path:
        """Persist configuration to JSON file."""
        path = (config_path or DEFAULT_CONFIG_PATH).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "downloads_dir": str(self.downloads_dir),
            "destination_dir": str(self.destination_dir),
            "grace_period_seconds": self.grace_period_seconds,
            "categories": self.categories,
            "ignore_extensions": sorted(list(self.ignore_extensions)),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return path

    def is_destination_available(self) -> bool:
        """Verify destination directory is mounted and writable."""
        return is_path_available(self.destination_dir)

    def is_downloads_available(self) -> bool:
        """Verify downloads directory is accessible."""
        return is_path_available(self.downloads_dir)

    def get_storage_info(self, target_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
        """Return disk usage details for a given path or destination directory."""
        target = (target_path or self.destination_dir).expanduser()
        try:
            curr = target
            while not curr.exists() and curr != curr.parent:
                curr = curr.parent
            if not curr.exists():
                return None
            usage = shutil.disk_usage(curr)
            return {
                "path": str(curr),
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
                "percent_used": round((usage.used / usage.total) * 100, 1) if usage.total else 0,
            }
        except Exception:
            return None
