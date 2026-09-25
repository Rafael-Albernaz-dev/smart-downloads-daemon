"""
Batch migration tool for moving organized categories across storage devices.
"""

import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .config import DaemonConfig, is_path_available

def format_bytes(num_bytes: int) -> str:
    """Format bytes into human-readable representation."""
    val = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(val) < 1024.0 or unit == "TB":
            return f"{val:3.1f} {unit}" if unit != "B" else f"{int(val)} B"
        val /= 1024.0
    return f"{val:.1f} PB"

@dataclass
class MigrationItem:
    category: str
    source_path: Path
    relative_path: Path
    target_path: Path
    size_bytes: int

@dataclass
class MigrationPlan:
    from_dir: Path
    to_dir: Path
    items: List[MigrationItem] = field(default_factory=list)
    categories_found: List[str] = field(default_factory=list)
    total_files: int = 0
    total_bytes: int = 0
    category_summary: Dict[str, Dict[str, int]] = field(default_factory=dict)

@dataclass
class MigrationResult:
    from_dir: Path
    to_dir: Path
    total_files_planned: int = 0
    files_moved: int = 0
    bytes_moved: int = 0
    directories_cleaned: int = 0
    collisions_resolved: int = 0
    errors: List[str] = field(default_factory=list)
    dry_run: bool = False

class Migrator:
    """Orchestrates scanning, safety verification, and execution of batch migrations."""

    def __init__(self, config: Optional[DaemonConfig] = None):
        self.config = config or DaemonConfig.load()

    def create_plan(
        self,
        from_dir: Optional[Path] = None,
        to_dir: Optional[Path] = None,
        categories: Optional[List[str]] = None,
    ) -> MigrationPlan:
        source_base = (from_dir or self.config.destination_dir).expanduser().resolve()
        target_base = (to_dir or self.config.destination_dir).expanduser().resolve()

        cat_names = categories or (list(self.config.categories.keys()) + ["Outros"])
        # Preserve order and avoid duplicates
        cat_names = list(dict.fromkeys(cat_names))

        plan = MigrationPlan(from_dir=source_base, to_dir=target_base)

        for cat in cat_names:
            src_cat_dir = source_base / cat
            if not src_cat_dir.is_dir():
                continue

            plan.categories_found.append(cat)
            cat_files = 0
            cat_bytes = 0

            for root, _, files in os.walk(src_cat_dir):
                for fname in files:
                    file_path = Path(root) / fname
                    if not file_path.is_file():
                        continue

                    try:
                        file_size = file_path.stat().st_size
                    except OSError:
                        file_size = 0

                    rel_path = file_path.relative_to(src_cat_dir)
                    target_path = target_base / cat / rel_path

                    item = MigrationItem(
                        category=cat,
                        source_path=file_path,
                        relative_path=rel_path,
                        target_path=target_path,
                        size_bytes=file_size,
                    )
                    plan.items.append(item)
                    cat_files += 1
                    cat_bytes += file_size

            plan.category_summary[cat] = {
                "files": cat_files,
                "bytes": cat_bytes,
            }
            plan.total_files += cat_files
            plan.total_bytes += cat_bytes

        return plan

    def execute_plan(
        self,
        plan: MigrationPlan,
        dry_run: bool = False,
        progress_callback: Optional[Callable[[MigrationItem, int, int], None]] = None,
    ) -> MigrationResult:
        result = MigrationResult(
            from_dir=plan.from_dir,
            to_dir=plan.to_dir,
            total_files_planned=plan.total_files,
            dry_run=dry_run,
        )

        if not is_path_available(plan.to_dir):
            result.errors.append(f"Destination '{plan.to_dir}' is not mounted, accessible or writable.")
            return result

        if dry_run:
            result.files_moved = plan.total_files
            result.bytes_moved = plan.total_bytes
            return result

        total = plan.total_files
        for idx, item in enumerate(plan.items, start=1):
            if progress_callback:
                progress_callback(item, idx, total)

            dest_folder = item.target_path.parent
            dest_folder.mkdir(parents=True, exist_ok=True)

            dest_file = item.target_path
            if dest_file.exists():
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                stem = item.target_path.stem
                suffix = item.target_path.suffix
                dest_file = dest_folder / f"{stem}_{timestamp}{suffix}"
                counter = 1
                while dest_file.exists():
                    dest_file = dest_folder / f"{stem}_{timestamp}_{counter}{suffix}"
                    counter += 1
                result.collisions_resolved += 1

            try:
                # Copy with metadata, verify size, then remove source
                shutil.copy2(item.source_path, dest_file)
                if dest_file.exists() and dest_file.stat().st_size == item.size_bytes:
                    item.source_path.unlink()
                    result.files_moved += 1
                    result.bytes_moved += item.size_bytes
                else:
                    result.errors.append(f"Verification failed after copy: {item.source_path} -> {dest_file}")
            except Exception as e:
                result.errors.append(f"Failed to move {item.source_path}: {e}")

        # Clean up empty directories in source category folders
        for cat in plan.categories_found:
            src_cat_dir = plan.from_dir / cat
            if not src_cat_dir.exists():
                continue

            for root, dirs, _ in os.walk(src_cat_dir, topdown=False):
                p = Path(root)
                try:
                    if p.exists() and not any(p.iterdir()):
                        p.rmdir()
                        result.directories_cleaned += 1
                except OSError:
                    pass

            try:
                if src_cat_dir.exists() and not any(src_cat_dir.iterdir()):
                    src_cat_dir.rmdir()
                    result.directories_cleaned += 1
            except OSError:
                pass

        return result
