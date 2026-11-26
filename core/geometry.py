"""Geometria do contorno da placa: reconstrução do polígono e regra de cotagem.

Regra comercial de cotas:
* Placas **retangulares ou quadradas**: cota pelos lados (X e Y).
* Placas em **L ou qualquer outro formato**: cota pelos lados de maior comprimento
  (maior lado horizontal, maior lado vertical e, se for o maior de todos, o maior
  lado inclinado). Se o maior lado reto for menor que o envelope (ex.: formato T,
  trapézio, placa redonda), a cota total máxima também é indicada, pois é ela que
  define a embalagem.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from core.parsers import GerberLayer

SNAP_TOL = 0.02          # mm: tolerância para ligar pontas de segmentos
COLLINEAR_DEG = 1.0      # graus: segmentos com desvio menor são fundidos num só lado
MIN_EDGE_FRAC = 0.05     # lados retos menores que 5% do envelope são ignorados nas cotas
RECT_AREA_RATIO = 0.97   # área do polígono / área do envelope para considerar retangular


@dataclass
class Edge:
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def length(self) -> float:
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)

    @property
    def angle(self) -> float:
        """Ângulo em graus, normalizado para [0, 180)."""
        return math.degrees(math.atan2(self.y2 - self.y1, self.x2 - self.x1)) % 180.0

    @property
    def orientation(self) -> str:
        a = self.angle
        if a < 1.0 or a > 179.0:
            return "H"
        if abs(a - 90.0) < 1.0:
            return "V"
        return "D"


@dataclass
class Dimension:
    """Cota a desenhar."""

    kind: str            # "H" | "V" | "D" (alinhada)
    value: float         # mm
    edge: Edge           # segmento cotado (para cotas de envelope: as extremidades)
    label: str
    level: int = 0       # 0 = junto à peça, 1 = segunda linha (cota total)
    side: str = ""       # "bottom" | "top" | "left" | "right" | "" (alinhada)


@dataclass
class OutlineShape:
    polygon: np.ndarray | None              # contorno principal (N x 2), ou None
    cutouts: list[np.ndarray] = field(default_factory=list)
    shape: str = "Indefinido"               # "Retangular" | "Quadrado" | "Irregular" | "Indefinido"
    real_area_mm2: float = 0.0
    edges: list[Edge] = field(default_factory=list)
    dimensions: list[Dimension] = field(default_factory=list)

    @property
    def is_rectangular(self) -> bool:
        return self.shape in ("Retangular", "Quadrado")


# --------------------------------------------------------------------------- #
def _poly_area(p: np.ndarray) -> float:
    x, y = p[:, 0], p[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def _key(x: float, y: float) -> tuple[int, int]:
    return (int(round(x / SNAP_TOL)), int(round(y / SNAP_TOL)))


def chain_loops(layer: GerberLayer) -> list[np.ndarray]:
    """Liga os traços do contorno em laços fechados."""
    loops: list[np.ndarray] = [r for r in layer.regions if len(r) >= 3]
    segs = [(t.x1, t.y1, t.x2, t.y2) for t in layer.tracks
            if math.hypot(t.x2 - t.x1, t.y2 - t.y1) > 1e-6]
    adj: dict[tuple[int, int], list[int]] = {}
    for i, (x1, y1, x2, y2) in enumerate(segs):
        adj.setdefault(_key(x1, y1), []).append(i)
        adj.setdefault(_key(x2, y2), []).append(i)
    used = [False] * len(segs)
    for start in range(len(segs)):
        if used[start]:
            continue
        used[start] = True
        x1, y1, x2, y2 = segs[start]
        pts = [(x1, y1), (x2, y2)]
        head = _key(x1, y1)
        cur = _key(x2, y2)
        closed = False
        for _ in range(len(segs)):
            if cur == head and len(pts) > 2:
                closed = True
                break
            nxt = next((j for j in adj.get(cur, []) if not used[j]), None)
            if nxt is None:
                break
            used[nxt] = True
            a1, b1, a2, b2 = segs[nxt]
            if _key(a1, b1) == cur:
                pts.append((a2, b2))
                cur = _key(a2, b2)
            else:
                pts.append((a1, b1))
                cur = _key(a1, b1)
        if cur == head and len(pts) > 3:
            closed = True
        if closed:
            loops.append(np.array(pts[:-1], dtype=float))
    return loops


def straight_edges(poly: np.ndarray) -> list[Edge]:
    """Funde segmentos colineares consecutivos (arcos ficam como segmentos curtos)."""
    n = len(poly)
    if n < 3:
        return []
    raw = [Edge(*poly[i], *poly[(i + 1) % n]) for i in range(n)]
    raw = [e for e in raw if e.length > 1e-6]

    def same_dir(a: Edge, b: Edge) -> bool:
        da = math.degrees(math.atan2(a.y2 - a.y1, a.x2 - a.x1))
        db = math.degrees(math.atan2(b.y2 - b.y1, b.x2 - b.x1))
        d = abs((da - db + 180) % 360 - 180)
        return d < COLLINEAR_DEG

    # começa num vértice de "quebra" para não partir um lado ao meio
    start = 0
    for i in range(len(raw)):
        if not same_dir(raw[i - 1], raw[i]):
            start = i
            break
    raw = raw[start:] + raw[:start]
    merged: list[Edge] = []
    for e in raw:
        if merged and same_dir(merged[-1], e):
            m = merged[-1]
            merged[-1] = Edge(m.x1, m.y1, e.x2, e.y2)
        else:
            merged.append(e)
    return merged


def _outward_side(poly: np.ndarray, e: Edge) -> str:
    """Lado (bottom/top/left/right) para onde aponta a normal externa do lado."""
    from matplotlib.path import Path

    path = Path(poly)
    mx, my = (e.x1 + e.x2) / 2, (e.y1 + e.y2) / 2
    eps = max(e.length * 0.01, 0.05)
    if e.orientation == "H":
        return "bottom" if not path.contains_point((mx, my - eps)) else "top"
    return "left" if not path.contains_point((mx - eps, my)) else "right"


def analyze_outline(layer: GerberLayer | None, bbox) -> OutlineShape:
    x0, y0, x1, y1 = bbox
    w, h = x1 - x0, y1 - y0
    env_area = max(w * h, 1e-9)
    loops = chain_loops(layer) if layer is not None else []
    if not loops:
        shape = OutlineShape(None, shape="Indefinido", real_area_mm2=env_area)
        shape.dimensions = _envelope_dims(bbox, level=0)
        return shape

    loops.sort(key=_poly_area, reverse=True)
    main = loops[0]
    cutouts = [l for l in loops[1:] if _poly_area(l) > 0.5]
    area = _poly_area(main) - sum(_poly_area(c) for c in cutouts)
    out = OutlineShape(main, cutouts, real_area_mm2=area)
    out.edges = straight_edges(main)

    if _poly_area(main) / env_area >= RECT_AREA_RATIO:
        out.shape = "Quadrado" if abs(w - h) <= 0.005 * max(w, h) else "Retangular"
        out.dimensions = _envelope_dims(bbox, level=0, prefix="")
        return out

    out.shape = "Irregular"
    min_len = MIN_EDGE_FRAC * max(w, h)
    candidates = [e for e in out.edges if e.length >= min_len]
    dims: list[Dimension] = []
    # lados muito curtos frente ao envelope (ex.: facetas de arcos) não definem a cota
    hs = [e for e in candidates if e.orientation == "H" and e.length >= 0.25 * w]
    vs = [e for e in candidates if e.orientation == "V" and e.length >= 0.25 * h]
    candidates = [e for e in candidates if e.orientation != "D" or e.length >= 0.25 * max(w, h)]
    best_h = max(hs, key=lambda e: e.length, default=None)
    best_v = max(vs, key=lambda e: e.length, default=None)
    if best_h:
        dims.append(Dimension("H", abs(best_h.x2 - best_h.x1), best_h, "maior lado X", 0,
                              _outward_side(main, best_h)))
    if best_v:
        dims.append(Dimension("V", abs(best_v.y2 - best_v.y1), best_v, "maior lado Y", 0,
                              _outward_side(main, best_v)))
    longest = max(candidates, key=lambda e: e.length, default=None)
    if longest is not None and longest.orientation == "D":
        dims.append(Dimension("D", longest.length, longest, "maior lado", 0))
    # Cota total do envelope quando o maior lado reto não a representa
    if not best_h or abs(best_h.x2 - best_h.x1) < w - 0.05:
        dims.append(Dimension("H", w, Edge(x0, y0, x1, y0), "total máx. X", 1, "bottom"))
    if not best_v or abs(best_v.y2 - best_v.y1) < h - 0.05:
        dims.append(Dimension("V", h, Edge(x0, y0, x0, y1), "total máx. Y", 1, "left"))
    out.dimensions = dims
    return out


def _envelope_dims(bbox, level: int = 0, prefix: str = "") -> list[Dimension]:
    x0, y0, x1, y1 = bbox
    return [Dimension("H", x1 - x0, Edge(x0, y0, x1, y0), "X", level, "bottom"),
            Dimension("V", y1 - y0, Edge(x0, y0, x0, y1), "Y", level, "left")]
