"""Importação de projetos Gerber/Drill.

Aceita um arquivo ``.zip`` (inclusive com zips aninhados) ou uma pasta raiz e
faz varredura recursiva, pois clientes enviam Gerbers e Drill na mesma pasta ou
em subpastas separadas (ex.: ``/Gerber`` e ``/Drill``).

Cada arquivo candidato tem o conteúdo "farejado" para decidir se é Gerber
(RS-274X/X2) ou Excellon. Arquivos irrelevantes (PDF, IPC-356, relatórios
``.txt`` que não são Excellon, imagens, planilhas...) são ignorados.
"""
from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

# Extensões descartadas sem nem abrir o arquivo.
IGNORED_EXTENSIONS = {
    ".pdf", ".ipc", ".html", ".htm", ".doc", ".docx", ".xls", ".xlsx",
    ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".svg", ".step", ".stp", ".dxf",
    ".dwg", ".rep", ".drr", ".log", ".rul", ".apr", ".lst", ".md", ".json",
    ".xml", ".pcbdoc", ".schdoc", ".prjpcb", ".kicad_pcb", ".brd", ".sch",
    ".rar", ".7z", ".exe", ".dll", ".ini", ".htm", ".extrep", ".rpt", ".bom",
}

SNIFF_BYTES = 64 * 1024

_RE_GERBER_FS = re.compile(r"%\s*(FS[LTD]?[AI]|MO(IN|MM))")
_RE_GERBER_D = re.compile(r"D0?[123]\*")
_RE_EXC_M48 = re.compile(r"^\s*M48\s*$", re.M)
_RE_EXC_TOOL = re.compile(r"^\s*T\d+(?:[FSB][\d.]+)*C\d*\.?\d+", re.M)
_RE_EXC_COORD = re.compile(r"^\s*(?:G\d+)?X[+-]?[\d.]+(?:Y[+-]?[\d.]+)?\s*$", re.M)
_RE_EXC_UNITS = re.compile(r"^\s*(METRIC|INCH)\b", re.M)


@dataclass
class SourceFile:
    """Arquivo de fabricação detectado."""

    path: Path          # caminho real no disco
    relpath: str        # caminho relativo ao projeto (exibição)
    fmt: str            # "gerber" | "excellon" | "pnp" (pick & place / centroide)

    @property
    def name(self) -> str:
        return self.path.name

    def read_text(self) -> str:
        return self.path.read_bytes().decode("latin-1", errors="replace")

    def head(self, size: int = SNIFF_BYTES) -> str:
        with open(self.path, "rb") as fh:
            return fh.read(size).decode("latin-1", errors="replace")


@dataclass
class ExtractedProject:
    """Resultado da importação."""

    source: Path
    root: Path
    files: list[SourceFile] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    _tempdir: str | None = None

    @property
    def project_name(self) -> str:
        return self.source.stem if self.source.is_file() else self.source.name

    def cleanup(self) -> None:
        if self._tempdir:
            shutil.rmtree(self._tempdir, ignore_errors=True)
            self._tempdir = None


def sniff_format(data: bytes) -> str | None:
    """Retorna ``"gerber"``, ``"excellon"`` ou ``None`` a partir do conteúdo."""
    if not data or b"\x00" in data[:4096]:
        return None
    text = data.decode("latin-1", errors="replace")
    if _RE_GERBER_FS.search(text) or (text.lstrip().startswith("G04") and _RE_GERBER_D.search(text)):
        return "gerber"
    if _RE_EXC_M48.search(text):
        return "excellon"
    if _RE_EXC_TOOL.search(text) and _RE_EXC_COORD.search(text):
        return "excellon"
    if _RE_EXC_UNITS.search(text) and _RE_EXC_COORD.search(text):
        return "excellon"
    return None


def detect_format(path: Path) -> str | None:
    try:
        with open(path, "rb") as fh:
            data = fh.read(SNIFF_BYTES)
    except OSError:
        return None
    fmt = sniff_format(data)
    if fmt is None and data and b"\x00" not in data[:4096]:
        from core.pnp import looks_like_pnp

        text = data.decode("latin-1", errors="replace")
        if path.suffix.lower() in (".mnt", ".mnb") or looks_like_pnp(text):
            fmt = "pnp"
    return fmt


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extrai protegendo contra *zip slip* (caminhos fora do destino)."""
    dest = dest.resolve()
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if name.startswith("__MACOSX/") or name.endswith("/"):
            continue
        target = (dest / name).resolve()
        if dest not in target.parents and target != dest:
            continue  # entrada maliciosa ou inválida
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)


def _expand_nested_zips(root: Path, depth: int = 0) -> None:
    if depth > 3:
        return
    for zpath in list(root.rglob("*")):
        if zpath.is_file() and zpath.suffix.lower() == ".zip" and zipfile.is_zipfile(zpath):
            sub = zpath.with_name(zpath.stem + "_zip")
            sub.mkdir(exist_ok=True)
            with zipfile.ZipFile(zpath) as zf:
                _safe_extract(zf, sub)
            zpath.unlink()
            _expand_nested_zips(sub, depth + 1)


def extract_project(source: str | Path) -> ExtractedProject:
    """Importa um ``.zip`` ou pasta e devolve os arquivos Gerber/Excellon encontrados."""
    source = Path(source)
    if not source.exists():
        raise FileNotFoundError(f"Origem não encontrada: {source}")

    tempdir = None
    if source.is_file():
        if not zipfile.is_zipfile(source):
            raise ValueError("Selecione um arquivo .zip ou uma pasta de projeto.")
        tempdir = tempfile.mkdtemp(prefix="vri_gq_")
        root = Path(tempdir)
        with zipfile.ZipFile(source) as zf:
            _safe_extract(zf, root)
        _expand_nested_zips(root)
    else:
        root = source

    project = ExtractedProject(source=source, root=root, _tempdir=tempdir)
    for path in sorted(root.rglob("*"), key=lambda p: str(p).lower()):
        if not path.is_file() or path.name.startswith("."):
            continue
        rel = path.relative_to(root).as_posix()
        if "__MACOSX" in rel:
            continue
        if path.suffix.lower() in IGNORED_EXTENSIONS:
            project.ignored.append(rel)
            continue
        fmt = detect_format(path)
        if fmt is None:
            project.ignored.append(rel)
            continue
        project.files.append(SourceFile(path=path, relpath=rel, fmt=fmt))
    return project
