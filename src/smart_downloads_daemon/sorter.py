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


def is_valid_file(filename: str, config: DaemonConfig) -> bool:
    """Check if a filename should be processed or skipped."""
    if not filename or filename.startswith(".") or filename.endswith("~") or filename.endswith("#"):
        return False
    fn_lower = filename.lower()
    for bad_ext in config.ignore_extensions:
        if fn_lower.endswith(bad_ext.lower()):
            return False
    return True


def get_destination_folder(
    filename: str,
    config: DaemonConfig,
    source_path: Optional[Path] = None,
) -> Path:
    """Determine destination category and subcategory folder."""
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
    """Move a file to its destination category/subcategory folder with collision resolution."""
    source_path = config.downloads_dir / filename
    if not source_path.is_file():
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

