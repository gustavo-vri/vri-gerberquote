"""Localização de recursos (assets) tanto no código-fonte quanto no app instalado.

No executável gerado pelo PyInstaller os arquivos ficam em ``sys._MEIPASS``.
"""
from __future__ import annotations

import sys
from pathlib import Path


def app_root() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base)
    return Path(__file__).resolve().parent.parent


def resource_path(relative: str) -> Path:
    return app_root() / relative
