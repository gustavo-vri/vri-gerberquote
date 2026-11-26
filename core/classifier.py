"""Classificação inteligente de camadas (Altium, KiCad, Eagle, OrCAD e outros CADs).

Ordem de decisão para arquivos Gerber:
1. Extensão padrão Protel/Altium (``.GTL``, ``.GBL``, ``.G1``, ``.GKO`` ...);
2. Atributo Gerber X2 ``%TF.FileFunction`` (quando presente);
3. Palavras-chave no nome do arquivo (``F_Cu``, ``top_copper``, ``inner`` ...).

Arquivos Excellon são separados em PTH (metalizado) e NPTH (não metalizado) pelo
nome (``-Plated`` / ``-NonPlated`` / ``NPTH``) ou pelo cabeçalho (``;TYPE=PLATED``,
``TF.FileFunction,NonPlated`` ...). Sem indicação, o arquivo é tratado como misto.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from core.extractor import SourceFile


class LayerType(str, Enum):
    OUTLINE = "Outline / Keep-Out"
    TOP_COPPER = "Top Copper"
    INNER = "Inner Layer"
    BOTTOM_COPPER = "Bottom Copper"
    TOP_SILK = "Top Silkscreen"
    BOTTOM_SILK = "Bottom Silkscreen"
    TOP_PASTE = "Top Paste (SMD)"
    BOTTOM_PASTE = "Bottom Paste (SMD)"
    TOP_MASK = "Top Solder Mask"
    BOTTOM_MASK = "Bottom Solder Mask"
    DRILL_PTH = "Drill PTH (Metalizado)"
    DRILL_NPTH = "Drill NPTH (Não Metalizado)"
    DRILL_MIXED = "Drill Misto (PTH/NPTH)"
    PICK_PLACE = "Pick & Place (centroide)"
    MECHANICAL = "Mecânica / Outros"
    IGNORE = "Ignorar"

    @property
    def is_drill(self) -> bool:
        return self in (LayerType.DRILL_PTH, LayerType.DRILL_NPTH, LayerType.DRILL_MIXED)

    @property
    def is_copper(self) -> bool:
        return self in (LayerType.TOP_COPPER, LayerType.BOTTOM_COPPER, LayerType.INNER)


GERBER_TYPES = [t for t in LayerType if not t.is_drill and t != LayerType.PICK_PLACE]
PNP_TYPES = [LayerType.PICK_PLACE, LayerType.IGNORE]
DRILL_TYPES = [LayerType.DRILL_PTH, LayerType.DRILL_NPTH, LayerType.DRILL_MIXED, LayerType.IGNORE]


@dataclass
class ClassifiedFile:
    source: SourceFile
    layer_type: LayerType
    reason: str = ""

    @property
    def name(self) -> str:
        return self.source.name


# --------------------------------------------------------------------------- #
# Regras por extensão (padrão Protel/Altium e alguns legados Eagle)
# --------------------------------------------------------------------------- #
_EXT_MAP = {
    "gko": LayerType.OUTLINE, "gm1": LayerType.OUTLINE, "gml": LayerType.OUTLINE,
    "gtl": LayerType.TOP_COPPER, "cmp": LayerType.TOP_COPPER,
    "gbl": LayerType.BOTTOM_COPPER, "sol": LayerType.BOTTOM_COPPER,
    "gto": LayerType.TOP_SILK, "plc": LayerType.TOP_SILK,
    "gbo": LayerType.BOTTOM_SILK, "pls": LayerType.BOTTOM_SILK,
    "gtp": LayerType.TOP_PASTE, "crc": LayerType.TOP_PASTE,
    "gbp": LayerType.BOTTOM_PASTE, "crs": LayerType.BOTTOM_PASTE,
    "gts": LayerType.TOP_MASK, "stc": LayerType.TOP_MASK,
    "gbs": LayerType.BOTTOM_MASK, "sts": LayerType.BOTTOM_MASK,
    # Pad master, drill drawing/guide: informativos, não entram na análise
    "gpt": LayerType.MECHANICAL, "gpb": LayerType.MECHANICAL,
    "gd1": LayerType.MECHANICAL, "gg1": LayerType.MECHANICAL,
}
_RE_INNER_EXT = re.compile(r"^(g|gp)\d{1,2}$")
_RE_MECH_EXT = re.compile(r"^gm\d{1,2}$")

# --------------------------------------------------------------------------- #
# Regras por palavras-chave (ordem importa: mais específicas primeiro)
# --------------------------------------------------------------------------- #
_S = r"[\s_\-.]*"  # separadores opcionais
_KEYWORD_RULES: list[tuple[LayerType, re.Pattern]] = [
    (LayerType.TOP_PASTE, re.compile(rf"top{_S}.*paste|paste{_S}.*top|f[_.]paste|tcream|cream{_S}top")),
    (LayerType.BOTTOM_PASTE, re.compile(rf"bot(tom)?{_S}.*paste|paste{_S}.*bot|b[_.]paste|bcream|cream{_S}bot")),
    (LayerType.TOP_SILK, re.compile(rf"top{_S}.*(silk|overlay|legend)|(silk|legend){_S}.*top|f[_.]silks?|f[_.]legend")),
    (LayerType.BOTTOM_SILK, re.compile(rf"bot(tom)?{_S}.*(silk|overlay|legend)|(silk|legend){_S}.*bot|b[_.]silks?|b[_.]legend")),
    (LayerType.TOP_MASK, re.compile(rf"top{_S}.*(solder)?{_S}mask|(solder)?mask{_S}.*top|f[_.]mask|tstop")),
    (LayerType.BOTTOM_MASK, re.compile(rf"bot(tom)?{_S}.*(solder)?{_S}mask|(solder)?mask{_S}.*bot|b[_.]mask|bstop")),
    (LayerType.TOP_COPPER, re.compile(rf"top{_S}.*copper|copper{_S}.*top|f[_.]cu|top{_S}layer|signal{_S}top")),
    (LayerType.BOTTOM_COPPER, re.compile(rf"bot(tom)?{_S}.*copper|copper{_S}.*bot|b[_.]cu|bot(tom)?{_S}layer|signal{_S}bot")),
    (LayerType.INNER, re.compile(r"inner|(?<![a-z])in\d+([_.]cu)?|plane\d*|mid{_S}?layer\d*".replace("{_S}", _S))),
    (LayerType.OUTLINE, re.compile(rf"outline|edge{_S}cuts|board|keep{_S}out|profile|contour|contorno|dimension|route")),
]

_RE_X2_FUNCTION = re.compile(r"%TF\.FileFunction,([^*%]+)\*?%")


def _classify_x2(head: str) -> LayerType | None:
    m = _RE_X2_FUNCTION.search(head)
    if not m:
        return None
    parts = [p.strip().lower() for p in m.group(1).split(",")]
    kind = parts[0]
    pos = parts[-1] if len(parts) > 1 else ""
    if kind == "copper":
        if "top" in parts:
            return LayerType.TOP_COPPER
        if "bot" in parts:
            return LayerType.BOTTOM_COPPER
        return LayerType.INNER
    table = {
        "paste": (LayerType.TOP_PASTE, LayerType.BOTTOM_PASTE),
        "legend": (LayerType.TOP_SILK, LayerType.BOTTOM_SILK),
        "soldermask": (LayerType.TOP_MASK, LayerType.BOTTOM_MASK),
    }
    if kind in table:
        top, bot = table[kind]
        return top if pos.startswith("top") else bot
    if kind in ("profile", "keepout"):
        return LayerType.OUTLINE
    if kind in ("plated", "nonplated"):
        return None  # Excellon/Gerber de furação: tratado na rotina de drill
    return LayerType.MECHANICAL


def classify_drill(name: str, head: str) -> tuple[LayerType, str]:
    """Separa arquivos Excellon em PTH, NPTH ou misto."""
    low = name.lower()
    if re.search(r"non[\s_\-]?plated|unplated|npth|non[\s_\-]?pth|nplt", low):
        return LayerType.DRILL_NPTH, "nome do arquivo (NonPlated/NPTH)"
    if re.search(r"plated|pth|plt", low):
        return LayerType.DRILL_PTH, "nome do arquivo (Plated/PTH)"
    h = head.upper()
    has_npth = bool(re.search(r"TYPE\s*=\s*NON[_\-]?PLATED|FILEFUNCTION,NONPLATED", h))
    has_pth = bool(re.search(r"TYPE\s*=\s*PLATED|FILEFUNCTION,PLATED", h))
    if has_npth and has_pth:  # Altium: seções ;TYPE=PLATED e ;TYPE=NON_PLATED no mesmo arquivo
        return LayerType.DRILL_MIXED, "cabeçalho (PLATED + NON_PLATED por ferramenta)"
    if has_npth:
        return LayerType.DRILL_NPTH, "cabeçalho (NON_PLATED)"
    if has_pth:
        return LayerType.DRILL_PTH, "cabeçalho (PLATED)"
    return LayerType.DRILL_MIXED, "sem indicação PTH/NPTH (misto)"


def classify_file(source: SourceFile) -> ClassifiedFile:
    head = source.head()
    if source.fmt == "pnp":
        return ClassifiedFile(source, LayerType.PICK_PLACE, "colunas Designator/X/Y")
    if source.fmt == "excellon":
        lt, why = classify_drill(source.name, head)
        return ClassifiedFile(source, lt, why)

    name = source.name.lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    if ext in _EXT_MAP:
        return ClassifiedFile(source, _EXT_MAP[ext], f"extensão .{ext.upper()}")
    if _RE_INNER_EXT.match(ext):
        return ClassifiedFile(source, LayerType.INNER, f"extensão .{ext.upper()}")
    if _RE_MECH_EXT.match(ext):
        return ClassifiedFile(source, LayerType.MECHANICAL, f"extensão .{ext.upper()}")

    x2 = _classify_x2(head)
    if x2 is not None:
        return ClassifiedFile(source, x2, "atributo Gerber X2")

    stem = name
    for lt, rx in _KEYWORD_RULES:
        if rx.search(stem):
            return ClassifiedFile(source, lt, "palavra-chave no nome")
    # Gerber de furação (ex.: KiCad "-PTH-drl.gbr") ou desconhecido
    if re.search(r"drill|drl", stem):
        return ClassifiedFile(source, LayerType.MECHANICAL, "desenho de furação (Gerber)")
    return ClassifiedFile(source, LayerType.MECHANICAL, "não identificado")


def classify_files(files: list[SourceFile]) -> list[ClassifiedFile]:
    return [classify_file(f) for f in files]


def count_layers(classified: list[ClassifiedFile]) -> int:
    """Top + Inner Layers + Bottom encontrados."""
    types = [c.layer_type for c in classified]
    total = types.count(LayerType.INNER)
    total += 1 if LayerType.TOP_COPPER in types else 0
    total += 1 if LayerType.BOTTOM_COPPER in types else 0
    return total
