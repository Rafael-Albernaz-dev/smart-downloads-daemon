"""
File classification, guard-rail filtering, subcategory resolution, and collision-safe moving.
"""

import shutil
import subprocess
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import DaemonConfig


def normalize_text(text: str) -> str:
    """Normalize text removing accents and converting to lowercase."""
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def extract_pdf_snippet(pdf_path: Path, max_chars: int = 1000) -> str:
    """Extract first-page text snippet from a PDF file using pdftotext if available."""
    try:
        res = subprocess.run(
            ["pdftotext", "-l", "1", str(pdf_path), "-"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if res.returncode == 0:
            return res.stdout[:max_chars]
    except Exception:
        pass
    return ""


def resolve_subcategory(
    category: str,
    filename: str,
    config: DaemonConfig,
    source_path: Optional[Path] = None,
) -> Optional[str]:
    """Determine subcategory name based on configured keyword rules and content inspection."""
    cat_rules = config.subcategories.get(category)
    if not cat_rules:
        return None

    name_norm = normalize_text(filename)

    # 1. Match by filename keywords
    for subcat_name, rule_def in cat_rules.items():
        keywords = rule_def.get("keywords", [])
        for kw in keywords:
            kw_norm = normalize_text(kw)
            if kw_norm and kw_norm in name_norm:
                return subcat_name

    # 2. For PDF files, inspect content if source_path is available
    if category == "PDF" and source_path and source_path.is_file():
        pdf_text = extract_pdf_snippet(source_path)
        if pdf_text:
            content_norm = normalize_text(pdf_text)
            for subcat_name, rule_def in cat_rules.items():
                content_kws = rule_def.get("content_keywords", [])
                for kw in content_kws:
                    kw_norm = normalize_text(kw)
                    if kw_norm and kw_norm in content_norm:
                        return subcat_name

    return None


def is_folder_in_flight(folder_path: Path, config: DaemonConfig) -> bool:
    """Check if a directory contains active or incomplete downloads."""
    try:
        for item in folder_path.rglob("*"):
            name_lower = item.name.lower()
            for bad_ext in config.ignore_extensions:
                if name_lower.endswith(bad_ext.lower()):
                    return True
    except Exception:
        pass
    return False


def is_valid_item(name: str, config: DaemonConfig, base_dir: Optional[Path] = None) -> bool:
    """Check if a file or directory should be processed or skipped."""
    if not name or name.startswith(".") or name.endswith("~") or name.endswith("#"):
        return False

    if base_dir is not None:
        target_path = base_dir / name
        if target_path.is_dir():
            if is_folder_in_flight(target_path, config):
                return False
            return True

    fn_lower = name.lower()
    for bad_ext in config.ignore_extensions:
        if fn_lower.endswith(bad_ext.lower()):
            return False
    return True


def is_valid_file(filename: str, config: DaemonConfig) -> bool:
    """Check if a filename should be processed or skipped (backward compatible)."""
    return is_valid_item(filename, config)


def get_destination_folder(
    filename: str,
    config: DaemonConfig,
    source_path: Optional[Path] = None,
) -> Path:
    """Determine destination category and subcategory folder for a file or directory."""
    is_dir = False
    if source_path is not None:
        is_dir = source_path.is_dir()
    elif (config.downloads_dir / filename).is_dir():
        is_dir = True

    if is_dir:
        # 1. Check if directory name matches any configured subcategory rules
        for cat in sorted(config.subcategories.keys()):
            subcat = resolve_subcategory(cat, filename, config, source_path=source_path)
            if subcat:
                return config.destination_dir / cat / subcat

        # 2. Check if "Pastas" is in configured categories or fallback to "Pastas"
        if "Pastas" in config.categories:
            return config.destination_dir / "Pastas"
        return config.destination_dir / "Pastas"

    ext = Path(filename).suffix.strip().lower().lstrip(".")
    target_category: Optional[str] = None

    if ext:
        for category, extensions in config.categories.items():
            if ext in extensions:
                target_category = category
                break

    if not target_category:
        return config.destination_dir / "Outros"

    subcat = resolve_subcategory(target_category, filename, config, source_path=source_path)
    if subcat:
        return config.destination_dir / target_category / subcat

    return config.destination_dir / target_category


def move_file(filename: str, config: DaemonConfig) -> Optional[Path]:
    """Move a file or folder to its destination category/subcategory folder with collision resolution."""
    source_path = config.downloads_dir / filename
    if not source_path.exists():
        return None

    is_dir = source_path.is_dir()
    if is_dir and is_folder_in_flight(source_path, config):
        return None

    if not config.is_destination_available():
        print(
            f"[WARNING] Destination '{config.destination_dir}' is unavailable (unmounted or unwritable). Retaining '{filename}' in Downloads.",
            file=sys.stderr,
        )
        sys.stderr.flush()
        return None

    dest_folder = get_destination_folder(filename, config, source_path=source_path)
    dest_folder.mkdir(parents=True, exist_ok=True)

    dest_file = dest_folder / filename
    if dest_file.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if is_dir:
            dest_file = dest_folder / f"{filename}_{timestamp}"
            counter = 1
            while dest_file.exists():
                dest_file = dest_folder / f"{filename}_{timestamp}_{counter}"
                counter += 1
        else:
            stem = source_path.stem.rstrip()
            suffix = source_path.suffix.strip()
            dest_file = dest_folder / f"{stem}_{timestamp}{suffix}"
            counter = 1
            while dest_file.exists():
                dest_file = dest_folder / f"{stem}_{timestamp}_{counter}{suffix}"
                counter += 1

    try:
        if is_dir:
            file_size = sum(f.stat().st_size for f in source_path.rglob("*") if f.is_file())
        else:
            file_size = source_path.stat().st_size
    except Exception:
        file_size = None

    try:
        shutil.move(str(source_path), str(dest_file))
        print(f"[OK] {filename} -> {dest_file}")
        sys.stdout.flush()

        try:
            from smart_downloads_daemon.history import record_event
            rel = dest_folder.relative_to(config.destination_dir)
            parts = rel.parts
            cat = parts[0] if parts else ("Pastas" if is_dir else "Outros")
            subcat = parts[1] if len(parts) > 1 else None
            record_event(
                config=config,
                action="move",
                source=filename,
                destination=dest_file,
                category=cat,
                subcategory=subcat,
                size_bytes=file_size,
                collision=(dest_file.name != filename),
            )
        except Exception:
            pass

        try:
            from smart_downloads_daemon.notifier import notify_organized
            notify_organized(
                source_name=filename,
                dest_path=dest_file,
                is_dir=is_dir,
                config=config,
            )
        except Exception:
            pass

        return dest_file
    except Exception as e:
        print(f"[ERROR] Failed to move {filename}: {e}", file=sys.stderr)
        sys.stderr.flush()
        return None


