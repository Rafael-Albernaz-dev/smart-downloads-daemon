"""
Reorganizer engine for classifying existing organized files into smart subcategories.
"""

import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .config import DaemonConfig
from .migrator import format_bytes
from .sorter import get_destination_folder, is_valid_file


@dataclass
class OrganizeItem:
    source_path: Path
    category: str
    target_dir: Path
    size_bytes: int


@dataclass
class OrganizePlan:
    target_dir: Path
    items: List[OrganizeItem] = field(default_factory=list)
    category_summary: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    total_files: int = 0
    total_bytes: int = 0


@dataclass
class OrganizeResult:
    dry_run: bool
    total_files_planned: int = 0
    files_moved: int = 0
    bytes_moved: int = 0
    collisions_resolved: int = 0
    errors: List[str] = field(default_factory=list)


class Organizer:
    """Manages batch reorganization of existing category files into subcategories."""

    def __init__(self, config: DaemonConfig):
        self.config = config

    def create_plan(self, target_dir: Optional[Path] = None) -> OrganizePlan:
        """Scan category roots and plan subcategory movements."""
        base_dir = (target_dir or self.config.destination_dir).expanduser().resolve()
        plan = OrganizePlan(target_dir=base_dir)

        # Only inspect managed categories
        managed_categories = set(self.config.categories.keys())

        for cat in sorted(managed_categories):
            cat_dir = base_dir / cat
            if not cat_dir.is_dir():
                continue

            subcat_counts: Dict[str, int] = {}
            cat_bytes = 0

            # Only inspect files sitting directly in the category root
            try:
                for entry in sorted(cat_dir.iterdir(), key=lambda p: p.name.lower()):
                    if not entry.is_file():
                        continue

                    if not is_valid_file(entry.name, self.config):
                        continue

                    # Determine target subcategory folder using source_path for content inspection
                    target_folder = get_destination_folder(entry.name, self.config, source_path=entry)

                    # If target is different from where file currently is:
                    if target_folder != cat_dir:
                        size = entry.stat().st_size
                        item = OrganizeItem(
                            source_path=entry,
                            category=cat,
                            target_dir=target_folder,
                            size_bytes=size,
                        )
                        plan.items.append(item)
                        subcat_name = target_folder.name
                        subcat_counts[subcat_name] = subcat_counts.get(subcat_name, 0) + 1
                        cat_bytes += size
            except Exception:
                pass

            if subcat_counts:
                plan.category_summary[cat] = {
                    "subcategories": subcat_counts,
                    "total_files": sum(subcat_counts.values()),
                    "total_bytes": cat_bytes,
                }

        plan.total_files = len(plan.items)
        plan.total_bytes = sum(i.size_bytes for i in plan.items)
        return plan

    def execute_plan(
        self,
        plan: OrganizePlan,
        dry_run: bool = True,
        progress_callback: Optional[Callable[[OrganizeItem, int, int], None]] = None,
    ) -> OrganizeResult:
        """Execute or simulate the reorganization plan."""
        result = OrganizeResult(
            dry_run=dry_run,
            total_files_planned=plan.total_files,
        )

        if dry_run or not plan.items:
            return result

        for idx, item in enumerate(plan.items, 1):
            if progress_callback:
                progress_callback(item, idx, plan.total_files)

            if not item.source_path.is_file():
                continue

            item.target_dir.mkdir(parents=True, exist_ok=True)
            target_file = item.target_dir / item.source_path.name

            # Collision resolution with timestamp
            if target_file.exists():
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                stem = item.source_path.stem.rstrip()
                suffix = item.source_path.suffix.strip()
                target_file = item.target_dir / f"{stem}_{timestamp}{suffix}"
                counter = 1
                while target_file.exists():
                    target_file = item.target_dir / f"{stem}_{timestamp}_{counter}{suffix}"
                    counter += 1
                result.collisions_resolved += 1

            try:
                shutil.move(str(item.source_path), str(target_file))
                result.files_moved += 1
                result.bytes_moved += item.size_bytes
            except Exception as e:
                result.errors.append(f"Failed to move {item.source_path} -> {target_file}: {e}")

        return result
