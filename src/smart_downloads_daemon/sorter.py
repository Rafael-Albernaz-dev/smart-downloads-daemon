"""
File classification, guard-rail filtering, and collision-safe moving.
"""

import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import DaemonConfig

def is_valid_file(filename: str, config: DaemonConfig) -> bool:
    """Check if a filename should be processed or skipped."""
    if filename.startswith(".") or filename.endswith("~") or filename.endswith("#"):
        return False
    fn_lower = filename.lower()
    for bad_ext in config.ignore_extensions:
        if fn_lower.endswith(bad_ext.lower()):
            return False
    return True

def get_destination_folder(filename: str, config: DaemonConfig) -> Path:
    """Determine the destination folder based on file extension."""
    ext = Path(filename).suffix.strip().lower().lstrip(".")
    if not ext:
        return config.destination_dir / "Outros"

    for category, extensions in config.categories.items():
        if ext in extensions:
            return config.destination_dir / category

    return config.destination_dir / "Outros"

def move_file(filename: str, config: DaemonConfig) -> Optional[Path]:
    """Move a file to its destination category folder with collision resolution."""
    source_path = config.downloads_dir / filename
    if not source_path.is_file():
        return None

    dest_folder = get_destination_folder(filename, config)
    dest_folder.mkdir(parents=True, exist_ok=True)

    dest_file = dest_folder / filename
    if dest_file.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = source_path.stem.rstrip()
        suffix = source_path.suffix.strip()
        dest_file = dest_folder / f"{stem}_{timestamp}{suffix}"
        counter = 1
        while dest_file.exists():
            dest_file = dest_folder / f"{stem}_{timestamp}_{counter}{suffix}"
            counter += 1

    try:
        shutil.move(str(source_path), str(dest_file))
        print(f"[OK] {filename} -> {dest_file}")
        sys.stdout.flush()
        return dest_file
    except Exception as e:
        print(f"[ERROR] Failed to move {filename}: {e}", file=sys.stderr)
        sys.stderr.flush()
        return None
