"""Leitura de arquivos Pick & Place (centroide) — fonte exata da contagem de componentes.

Formatos suportados:
* Altium ``Pick Place for <projeto>.txt`` / ``.csv`` (atual: "Designator","Comment","Layer",
  "Footprint","Center-X(mm)"... e legado: Designator Footprint Mid X Mid Y ... TB/Layer);
* KiCad ``*.pos`` (texto) e ``*-pos.csv`` / ``*-top-pos.csv`` (Ref,Val,Package,PosX,PosY,Rot,Side);
* Eagle ``*.mnt`` (top) / ``*.mnb`` (bottom);
* CSV genérico com colunas de designador, X, Y e lado.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

_RE_DESIG = re.compile(r"^(designator|ref|refdes|reference|ref\.?\s*des\.?|part|comp(onent)?|name)$", re.I)
_RE_X = re.compile(r"^(center[-_ ]?x|mid[-_ ]?x|pos[-_ ]?x|x|centerx|location[-_ ]?x|ref[-_ ]?x)(\(.*\))?$", re.I)
_RE_Y = re.compile(r"^(center[-_ ]?y|mid[-_ ]?y|pos[-_ ]?y|y|centery|location[-_ ]?y|ref[-_ ]?y)(\(.*\))?$", re.I)
_RE_SIDE = re.compile(r"^(layer|side|tb|top/bottom|placement side|mount side)$", re.I)
_RE_FOOT = re.compile(r"^(footprint|package|pattern|case|pcb footprint)$", re.I)
_RE_VAL = re.compile(r"^(comment|val|value|part ?number|description)$", re.I)
_RE_NUM = re.compile(r"^\s*([+-]?\d+(?:[.,]\d+)?)\s*(mm|mil)?\s*$", re.I)

# Designadores que não são montados (fiduciais, furos de fixação, logos)
_RE_NOT_ASSEMBLED = re.compile(r"^(FID|FD|FM|MH|MTG|HOLE|LOGO|REF\*|TP_?NC)", re.I)
_RE_NOT_ASSEMBLED_FP = re.compile(r"fiducial|mountinghole|mounting_hole|mtg|logo|testpoint_pad_nc", re.I)


@dataclass
class Placement:
    designator: str
    x: float
    y: float
    side: str                 # "TOP" | "BOT"
    footprint: str = ""
    value: str = ""


@dataclass
class PickPlaceFile:
    name: str
    placements: list[Placement] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)   # fiduciais, furos de fixação...
    warnings: list[str] = field(default_factory=list)


def _side(text: str, default: str = "TOP") -> str:
    t = (text or "").strip().lower()
    if t.startswith(("b", "bot", "back")) or "bottom" in t:
        return "BOT"
    if t.startswith(("t", "top", "front", "f")):
        return "TOP"
    return default


def _num(text: str, factor: float) -> float | None:
    m = _RE_NUM.match((text or "").replace('"', ""))
    if not m:
        return None
    v = float(m.group(1).replace(",", "."))
    unit = (m.group(2) or "").lower()
    if unit == "mil":
        return v * 0.0254
    if unit == "mm":
        return v
    return v * factor


def _normalize_header(line: str) -> str:
    # "Mid X" -> "Mid-X" (formato legado do Altium separado por espaços)
    return re.sub(r"\b(Mid|Ref|Pad|Center|Pos|Location)\s+([XY])\b", r"\1-\2", line, flags=re.I)


def _split(line: str, delim: str | None) -> list[str]:
    if delim:
        return [c.strip().strip('"').strip() for c in next(csv.reader(io.StringIO(line), delimiter=delim))]
    return line.split()


def looks_like_pnp(text: str) -> bool:
    return _find_header(text.splitlines()[:80]) is not None


def _find_header(lines: list[str]):
    for i, raw in enumerate(lines):
        line = _normalize_header(raw.strip().lstrip("#").strip())
        if not line:
            continue
        delim = None
        for d in (",", ";", "\t"):
            if line.count(d) >= 2:
                delim = d
                break
        try:
            cols = _split(line, delim)
        except (csv.Error, StopIteration):
            continue
        idx = {}
        for j, c in enumerate(cols):
            c2 = c.strip()
            if "desig" not in idx and _RE_DESIG.match(c2):
                idx["desig"] = j
            elif "x" not in idx and _RE_X.match(c2):
                idx["x"] = j
            elif "y" not in idx and _RE_Y.match(c2):
                idx["y"] = j
            elif "side" not in idx and _RE_SIDE.match(c2):
                idx["side"] = j
            elif "foot" not in idx and _RE_FOOT.match(c2):
                idx["foot"] = j
            elif "val" not in idx and _RE_VAL.match(c2):
                idx["val"] = j
        if {"desig", "x", "y"} <= idx.keys():
            return i, delim, idx, line
    return None


def parse_pnp(text: str, name: str = "") -> PickPlaceFile:
    out = PickPlaceFile(name=name)
    lines = text.splitlines()
    low_name = name.lower()

    # Eagle .mnt / .mnb: sem cabeçalho -> nome x y rot valor pacote (mm)
    if low_name.endswith((".mnt", ".mnb")):
        side = "BOT" if low_name.endswith(".mnb") else "TOP"
        for raw in lines:
            p = raw.split()
            if len(p) >= 3 and _num(p[1], 1.0) is not None and _num(p[2], 1.0) is not None:
                _add(out, Placement(p[0], _num(p[1], 1.0), _num(p[2], 1.0), side,
                                    p[5] if len(p) > 5 else "", p[4] if len(p) > 4 else ""))
        return out

    found = _find_header(lines)
    if found is None:
        out.warnings.append("Cabeçalho de Pick & Place não reconhecido.")
        return out
    hi, delim, idx, header = found
    head_text = "\n".join(lines[:hi + 1]).lower()
    factor = 0.0254 if ("(mil)" in header.lower() or re.search(r"units?\s*(used)?\s*[:=]\s*mil", head_text)) else 1.0
    default_side = "BOT" if re.search(r"bot(tom)?[-_]?pos|side\s*:\s*bottom", low_name + head_text) else "TOP"
    need = max(idx.values())
    for raw in lines[hi + 1:]:
        line = raw.strip()
        if not line or line.startswith(("#", "=", "-")):
            continue
        try:
            cols = _split(line, delim)
        except csv.Error:
            continue
        if len(cols) <= max(idx["desig"], idx["x"], idx["y"]):
            continue
        x, y = _num(cols[idx["x"]], factor), _num(cols[idx["y"]], factor)
        if x is None or y is None:
            continue
        get = lambda k: cols[idx[k]] if k in idx and idx[k] < len(cols) else ""  # noqa: E731
        _ = need
        _add(out, Placement(cols[idx["desig"]].strip(), x, y, _side(get("side"), default_side),
                            get("foot"), get("val")))
    return out


def _add(out: PickPlaceFile, p: Placement) -> None:
    if not p.designator:
        return
    if _RE_NOT_ASSEMBLED.match(p.designator) or _RE_NOT_ASSEMBLED_FP.search(p.footprint):
        out.excluded.append(p.designator)
        return
    out.placements.append(p)
