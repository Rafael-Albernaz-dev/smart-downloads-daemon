"""
Configuration loader, storage discovery, pause control, and category mapping definitions.
"""

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
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

DEFAULT_SUBCATEGORIES: Dict[str, Dict[str, Dict[str, List[str]]]] = {
    "PDF": {
        "Ordem de Servico": {
            "keywords": ["ordem de servico", "ordem tecnica", "os", "atendimento", "relatorio_lucas"],
            "content_keywords": [
                "ordem tecnica de servico",
                "ordem de servico",
                "dados do cliente",
                "aberto por:",
                "remuneracao por o.s",
            ],
        },
        "Boletos": {
            "keywords": [
                "boleto",
                "segundavia",
                "segundasvia",
                "fatura",
                "saneamento",
                "duplicata",
                "mensalidade",
                "guia",
                "comprovante",
            ],
            "content_keywords": [
                "linha digitavel",
                "beneficiario",
                "comprovante de pagamento",
                "companhia de saneamento",
                "vencimento",
            ],
        },
        "DRS": {
            "keywords": ["drs", "documento mestre", "documento-mestre"],
            "content_keywords": ["drs executivo", "documento mestre"],
        },
        "Atlas": {
            "keywords": ["atlas", "viabilidade", "ptp-manual", "ptp", "atlas-platform"],
            "content_keywords": ["atlas viabilidade", "atlas platform", "ptp manual"],
        },
        "Marketing": {
            "keywords": [
                "marketing",
                "metricas",
                "funis",
                "landing page",
                "lp",
                "conversao",
                "banner",
                "logo",
                "guia compacto",
            ],
            "content_keywords": ["marketing", "landing page", "conversao", "playhub", "canais"],
        },
        "Contratos": {
            "keywords": ["contrato", "termo de adesao", "termo", "adesao"],
            "content_keywords": ["contrato de prestacao", "termo de adesao", "clausula"],
        },
        "Documentos Empresa": {
            "keywords": ["cnpj", "cartao cnpj", "onboarding", "institucional"],
            "content_keywords": ["cadastro nacional da pessoa juridica", "comprovante de inscricao"],
        },
    },
    "Planilhas": {
        "OS e Atendimentos": {
            "keywords": ["os-diarias", "os_diarias", "relatorio-os", "relatorio_os", "suporte tecnico", "formulario de suporte"],
        },
        "Cobranca e Negativacao": {
            "keywords": ["negativad", "cobranca", "protesto", "suspensos", "cancelad", "starlink"],
        },
        "Equipamentos e Estoque": {
            "keywords": ["retirada", "estoque", "equipamento", "equips", "torres", "torre"],
        },
        "Clientes e Contratos": {
            "keywords": ["cliente", "contrato", "autenticacao", "localizac", "plano", "campanha"],
        },
    },
    "Imagens": {
        "ChatGPT IA": {
            "keywords": ["chatgpt", "midjourney", "dall-e", "imagem do chatgpt"],
        },
        "Logos e Icones": {
            "keywords": ["logo", "icone", "icon"],
        },
        "Banners e Marketing": {
            "keywords": ["banner", "camera", "alarme", "conecta", "indique e ganhe", "negociar", "propaganda"],
        },
        "Screenshots e Prints": {
            "keywords": ["screenshot", "captura", "2026-09-", "2026-10-", "2026-11-", "2026-12-", "2026-08-", "2025-"],
        },
    },
    "Textos": {
        "Atlas e Desenvolvimento": {
            "keywords": [
                "atlas",
                "roadmap",
                "auditoria",
                "vite",
                "spec",
                "dev_orchestrator",
                "skills",
                "hermes",
                "radar",
                "codex",
                "mcp",
                "soul",
                "prompt",
                "typescript",
            ],
        },
        "Rotas e Operacional": {
            "keywords": [
                "rota",
                "agenda",
                "suporte",
                "equipamento",
                "equips",
                "enlaces",
                "fibra",
                "instalacao",
                "custo",
            ],
        },
        "Clientes e Atendimento": {
            "keywords": ["cliente", "cancelamento", "dossie", "feedback", "atendimento"],
        },
    },
}

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "smart-downloads-daemon"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.json"
DEFAULT_PAUSE_PATH = DEFAULT_CONFIG_DIR / "paused"
DEFAULT_STATE_PATH = DEFAULT_CONFIG_DIR / "state.json"
DEFAULT_LOCK_PATH = DEFAULT_CONFIG_DIR / "daemon.lock"


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


def find_block_device_for_path(path: Path) -> Optional[Path]:
    """
    Detect if a path refers to a /media or /mnt partition with a UUID or label,
    and find the underlying block device (e.g. /dev/disk/by-uuid/<UUID>).
    """
    try:
        p = path.expanduser()
        parts = p.parts
        if len(parts) >= 3 and parts[1] in ("media", "mnt"):
            target_name = parts[3] if len(parts) >= 4 else parts[2]
            by_uuid = Path(f"/dev/disk/by-uuid/{target_name}")
            if by_uuid.exists():
                return by_uuid
            by_label = Path(f"/dev/disk/by-label/{target_name}")
            if by_label.exists():
                return by_label
    except Exception:
        pass
    return None


def try_automount_path(path: Path) -> bool:
    """
    Attempt to automatically mount an unmounted partition via udisksctl.
    """
    dev = find_block_device_for_path(path)
    if not dev:
        return False
    try:
        res = subprocess.run(
            ["udisksctl", "mount", "-b", str(dev), "--no-user-interaction"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0:
            return is_path_available(path)
    except Exception:
        pass
    return False


@dataclass
class DaemonConfig:
    downloads_dir: Path = field(default_factory=lambda: Path.home() / "Downloads")
    destination_dir: Path = field(default_factory=lambda: Path.home() / "Documents")
    grace_period_seconds: int = 300
    categories: Dict[str, List[str]] = field(default_factory=lambda: dict(DEFAULT_CATEGORIES))
    subcategories: Dict[str, Dict[str, Dict[str, List[str]]]] = field(
        default_factory=lambda: {
            cat: {sub: dict(defs) for sub, defs in subs.items()}
            for cat, subs in DEFAULT_SUBCATEGORIES.items()
        }
    )
    ignore_extensions: Set[str] = field(default_factory=lambda: set(DEFAULT_IGNORE_EXTENSIONS))
    config_dir: Path = field(default_factory=lambda: DEFAULT_CONFIG_DIR)

    @property
    def pause_file(self) -> Path:
        return self.config_dir / "paused"

    @property
    def state_file(self) -> Path:
        return self.config_dir / "state.json"

    @property
    def lock_file(self) -> Path:
        return self.config_dir / "daemon.lock"

    @classmethod
    def load(cls, config_path: Optional[Path] = None) -> "DaemonConfig":
        path = config_path or DEFAULT_CONFIG_PATH
        cfg = cls()
        if config_path is not None:
            cfg.config_dir = config_path.parent
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
                if "subcategories" in data and isinstance(data["subcategories"], dict):
                    cfg.subcategories = data["subcategories"]
                if "ignore_extensions" in data and isinstance(data["ignore_extensions"], list):
                    cfg.ignore_extensions = set(data["ignore_extensions"])
            except Exception as e:
                print(f"[WARNING] Could not parse config from {path}: {e}")
        return cfg

    def save(self, config_path: Optional[Path] = None) -> Path:
        """Persist configuration to JSON file."""
        path = (config_path or (self.config_dir / "config.json")).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "downloads_dir": str(self.downloads_dir),
            "destination_dir": str(self.destination_dir),
            "grace_period_seconds": self.grace_period_seconds,
            "categories": self.categories,
            "subcategories": self.subcategories,
            "ignore_extensions": sorted(list(self.ignore_extensions)),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return path

    def is_paused(self) -> bool:
        """Check if sorting daemon is currently paused."""
        return self.pause_file.is_file()

    def set_paused(self, paused: bool, reason: str = "") -> None:
        """Set or unset the paused state."""
        if paused:
            self.pause_file.parent.mkdir(parents=True, exist_ok=True)
            info = {
                "paused_at": datetime.now().isoformat(),
                "timestamp": time.time(),
                "reason": reason or "Paused by user",
            }
            with open(self.pause_file, "w", encoding="utf-8") as f:
                json.dump(info, f, indent=2)
        else:
            if self.pause_file.is_file():
                try:
                    self.pause_file.unlink()
                except OSError:
                    pass

    def get_pause_info(self) -> Optional[Dict[str, Any]]:
        """Return details about pause state if paused."""
        if not self.is_paused():
            return None
        try:
            with open(self.pause_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"paused_at": "unknown", "reason": "Paused by user"}

    def is_destination_available(self) -> bool:
        """Verify destination directory is mounted and writable."""
        return is_path_available(self.destination_dir)

    def is_downloads_available(self) -> bool:
        """Verify downloads directory is accessible."""
        return is_path_available(self.downloads_dir)

    def try_automount_destination(self) -> bool:
        """Attempt to mount destination directory if it is an unmounted block device."""
        if self.is_destination_available():
            return True
        return try_automount_path(self.destination_dir)

    def get_destination_device(self) -> Optional[Path]:
        """Return the block device corresponding to the destination directory if any."""
        return find_block_device_for_path(self.destination_dir)

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
