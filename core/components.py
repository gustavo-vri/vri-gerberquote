"""Contagem de COMPONENTES (não de pads/furos) para cotação de tempo de montagem.

Exemplo: um resistor 0603 tem 2 pads SMD, mas é 1 componente; um SOIC-8 tem 8 pads e é
1 componente; um DIP-14 tem 14 furos e é 1 componente PTH.

Fontes, em ordem de confiabilidade:
1. **Arquivo Pick & Place (centroide)** enviado pelo cliente (Altium, KiCad, Eagle, CSV):
   contagem exata por designador (R1, C5, U3...). Cada componente é classificado como SMD ou
   PTH pelo nome do footprint e, na dúvida, pela geometria (pads de pasta x furos metalizados).
2. **Estimativa geométrica** (sem Pick & Place): os pads da camada Paste e os furos PTH de
   componente são agrupados em componentes por padrão de encapsulamento:
   pares de pads iguais (passivos), fileiras com passo constante (pinos de CI), fileiras
   paralelas/opostas com o mesmo centro (SOIC, QFP, QFN), pad térmico interno, SOT-23,
   matriz de esferas (BGA); para PTH: fileiras de passo 2,54/5,08 mm, DIP (duas fileiras
   paralelas), pares axiais/radiais. Os **contornos fechados da serigrafia** (corpo do
   componente) unem grupos que pertencem ao mesmo componente (SOT, cristais, relés, trafos).

Os textos da serigrafia (ex.: "R1") são traços vetoriais no Gerber — não há texto legível
sem OCR; por isso o Pick & Place é a fonte recomendada (é o mesmo arquivo usado para
programar a Fuji AIM).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

from core.geometry import chain_loops
from core.parsers import GerberLayer
from core.pnp import Placement

try:  # contornos da serigrafia (opcional: sem shapely, agrupa só pelo passo dos furos)
    import shapely

    HAS_SHAPELY = True
except ImportError:  # pragma: no cover
    shapely = None
    HAS_SHAPELY = False

# Categorias usadas no cálculo de tempo
CAT_CHIP = "Chip (2 terminais)"
CAT_SMALL = "SOT / pequeno (3–6 terminais)"
CAT_IC = "CI / multipinos (7+ terminais)"
CAT_PTH = "PTH"


@dataclass
class Component:
    kind: str                 # "SMD" | "PTH"
    side: str                 # "TOP" | "BOT"
    x: float
    y: float
    bbox: tuple[float, float, float, float]
    terminals: int            # pads (SMD) ou terminais/furos (PTH)
    designator: str = ""
    footprint: str = ""
    value: str = ""
    signature: str = ""       # identifica o "tipo" (para estimar alimentadores/feeders)

    @property
    def category(self) -> str:
        if self.kind == "PTH":
            return CAT_PTH
        if self.terminals <= 2:
            return CAT_CHIP
        if self.terminals <= 6:
            return CAT_SMALL
        return CAT_IC


@dataclass
class ComponentCount:
    components: list[Component] = field(default_factory=list)
    source: str = ""                      # descrição da fonte
    exact: bool = False                   # True quando veio do Pick & Place
    excluded: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def count(self, kind: str, side: str | None = None) -> int:
        return sum(1 for c in self.components if c.kind == kind and (side is None or c.side == side))

    @property
    def smd_top(self) -> int:
        return self.count("SMD", "TOP")

    @property
    def smd_bot(self) -> int:
        return self.count("SMD", "BOT")

    @property
    def pth(self) -> int:
        return self.count("PTH")

    def by_category(self, kind: str, side: str | None = None) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.components:
            if c.kind == kind and (side is None or c.side == side):
                out[c.category] = out.get(c.category, 0) + 1
        return out

    def terminals(self, kind: str, side: str | None = None) -> int:
        return sum(c.terminals for c in self.components if c.kind == kind and (side is None or c.side == side))

    def distinct_types(self, kind: str, side: str | None = None) -> int:
        return len({c.signature for c in self.components if c.kind == kind and (side is None or c.side == side)})


# =========================================================================== #
# Utilidades
# =========================================================================== #
@dataclass
class _Pad:
    x: float
    y: float
    w: float
    h: float


def _pads(layer: GerberLayer | None) -> list[_Pad]:
    if layer is None:
        return []
    out = []
    for f in layer.flashes:
        hx, hy = f.aperture.half_size()
        out.append(_Pad(f.x, f.y, max(2 * hx, 0.05), max(2 * hy, 0.05)))
    for r in layer.regions:
        x0, y0 = r.min(axis=0)
        x1, y1 = r.max(axis=0)
        out.append(_Pad((x0 + x1) / 2, (y0 + y1) / 2, max(x1 - x0, 0.05), max(y1 - y0, 0.05)))
    return out


class _UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra

    def groups(self, items=None):
        g: dict[int, list[int]] = {}
        for i in (items if items is not None else range(len(self.p))):
            g.setdefault(self.find(i), []).append(i)
        return list(g.values())


def _same_size(a: _Pad, b: _Pad, tol: float = 0.12) -> bool:
    sa, sb = sorted((a.w, a.h)), sorted((b.w, b.h))
    return all(abs(p - q) <= tol * max(p, q) + 0.02 for p, q in zip(sa, sb))


def _bbox(pts_sizes) -> tuple[float, float, float, float]:
    x0 = min(x - w / 2 for x, y, w, h in pts_sizes)
    y0 = min(y - h / 2 for x, y, w, h in pts_sizes)
    x1 = max(x + w / 2 for x, y, w, h in pts_sizes)
    y1 = max(y + h / 2 for x, y, w, h in pts_sizes)
    return (x0, y0, x1, y1)


def _row_info(pts: np.ndarray):
    """Retorna (direção unitária, comprimento, passo médio, colinear?) de um grupo de pontos."""
    if len(pts) < 2:
        return np.array([1.0, 0.0]), 0.0, 0.0, True
    c = pts.mean(axis=0)
    u, s, vt = np.linalg.svd(pts - c)
    d = vt[0]
    proj = (pts - c) @ d
    perp = (pts - c) @ np.array([-d[1], d[0]])
    order = np.sort(proj)
    steps = np.diff(order)
    length = float(order[-1] - order[0])
    pitch = float(steps.mean()) if len(steps) else 0.0
    collinear = float(np.abs(perp).max()) <= max(0.15 * pitch, 0.05) if pitch else True
    uniform = bool(len(steps) == 0 or (steps.max() - steps.min()) <= 0.05 * pitch + 0.01)
    return d, length, pitch, collinear and uniform


# =========================================================================== #
# Estimativa geométrica — SMD
# =========================================================================== #
def _cluster_smd(pads: list[_Pad]) -> list[list[int]]:
    n = len(pads)
    if n == 0:
        return []
    P = np.array([(p.x, p.y) for p in pads])
    uf = _UF(n)
    elong = []
    for p in pads:
        if p.w < 0.87 * p.h:
            elong.append("x")        # eixo curto = x: pinos vizinhos ficam lado a lado em x
        elif p.h < 0.87 * p.w:
            elong.append("y")
        else:
            elong.append("sq")

    # Fase 1: pads alongados -> vizinhos ao longo do eixo curto (fileiras de pinos e pares)
    for i, a in enumerate(pads):
        if elong[i] == "sq":
            continue
        d = P - P[i]
        for j in np.nonzero((np.abs(d[:, 0]) < 3 * max(a.w, a.h)) & (np.abs(d[:, 1]) < 3 * max(a.w, a.h)))[0]:
            j = int(j)
            if j <= i or elong[j] != elong[i] or not _same_size(a, pads[j]):
                continue
            dx, dy = abs(d[j, 0]), abs(d[j, 1])
            if elong[i] == "x" and dy <= 0.15 * a.h and dx <= 2.4 * a.w:
                uf.union(i, j)
            elif elong[i] == "y" and dx <= 0.15 * a.w and dy <= 2.4 * a.h:
                uf.union(i, j)

    # Fase 1b: cadeias colineares com passo ALTERNADO (passivos encostados ponta a ponta) não são
    # fileiras de CI: divide em pares consecutivos a partir de uma das pontas.
    for g in uf.groups():
        if len(g) < 3 or elong[g[0]] == "sq":
            continue
        d, length, pitch, ok = _row_info(P[g])
        if ok:
            continue
        proj = (P[g] - P[g].mean(axis=0)) @ d
        perp = (P[g] - P[g].mean(axis=0)) @ np.array([-d[1], d[0]])
        if float(np.abs(perp).max()) > 0.2 * max(pads[g[0]].w, pads[g[0]].h):
            continue
        order = [g[k] for k in np.argsort(proj)]
        for k in order:
            uf.p[k] = k
        for a in range(0, len(order) - 1, 2):
            uf.union(order[a], order[a + 1])

    # Fase 2: pads quadrados/redondos -> matriz (BGA) ou pares pelo vizinho mútuo mais próximo
    sq = [i for i in range(n) if elong[i] == "sq"]
    if sq:
        uf_sq = _UF(n)
        for i in sq:
            a = pads[i]
            s = max(a.w, a.h)
            for j in sq:
                if j <= i or not _same_size(a, pads[j]):
                    continue
                dx, dy = abs(P[j, 0] - P[i, 0]), abs(P[j, 1] - P[i, 1])
                if (dy <= 0.15 * s and dx <= 2.2 * s) or (dx <= 0.15 * s and dy <= 2.2 * s):
                    uf_sq.union(i, j)
        for g in uf_sq.groups(sq):
            xs = np.unique(np.round(P[g, 0], 2))
            ys = np.unique(np.round(P[g, 1], 2))
            if len(g) >= 9 and len(xs) >= 3 and len(ys) >= 3:      # matriz de esferas (BGA)
                for k in g[1:]:
                    uf.union(g[0], k)
        # pares por vizinho mútuo mais próximo (mesmo tamanho)
        singles = [i for i in sq if len([k for k in range(n) if uf.find(k) == uf.find(i)]) == 1]
        _pair_mutual(pads, P, singles, uf, max_factor=3.2)

    # Fase 3: fileiras paralelas que se encaram (SOIC/SSOP) e grupos de mesmo centro (QFP/QFN)
    def rows():
        out = []
        for g in uf.groups():
            if len(g) >= 3:
                d, length, pitch, ok = _row_info(P[g])
                if ok:
                    out.append((g, d, length, pitch))
        return out

    rws = rows()
    for a_i in range(len(rws)):
        ga, da, la, pa = rws[a_i]
        ca = P[ga].mean(axis=0)
        for b_i in range(a_i + 1, len(rws)):
            gb, db, lb, pb = rws[b_i]
            if uf.find(ga[0]) == uf.find(gb[0]) or not _same_size(pads[ga[0]], pads[gb[0]]):
                continue
            if abs(abs(float(da @ db)) - 1) > 0.02 or abs(pa - pb) > 0.15 * max(pa, pb):
                continue
            o = P[gb].mean(axis=0) - ca
            along = abs(float(o @ da))
            perp = abs(float(o @ np.array([-da[1], da[0]])))
            plong = max(pads[ga[0]].w, pads[ga[0]].h)
            if along <= pa * 0.6 and plong < perp <= max(4.0 * plong, 1.5 * max(la, lb) + 2.0):
                uf.union(ga[0], gb[0])
    # mesmo centro -> mesmo CI (QFP/QFN com 4 fileiras)
    groups = [g for g in uf.groups() if len(g) >= 6]
    for i in range(len(groups)):
        for j in range(i + 1, len(groups)):
            ci, cj = P[groups[i]].mean(axis=0), P[groups[j]].mean(axis=0)
            s = min(min(pads[groups[i][0]].w, pads[groups[i][0]].h), 1.0)
            if np.hypot(*(ci - cj)) <= max(0.3, s):
                uf.union(groups[i][0], groups[j][0])

    # Fase 4: pad térmico / janelas de pasta dentro do corpo de um CI
    ics = [g for g in uf.groups() if len(g) >= 6]
    for g in ics:
        x0, y0 = P[g].min(axis=0)
        x1, y1 = P[g].max(axis=0)
        for h in uf.groups():
            if uf.find(h[0]) == uf.find(g[0]) or len(h) > 9:
                continue
            c = P[h].mean(axis=0)
            if x0 < c[0] < x1 and y0 < c[1] < y1:
                uf.union(g[0], h[0])

    # Fase 5: fileira de 3+ pinos + par/pad oposto (SOT-23-5, SOT-223, DPAK)
    for g, d, length, pitch in rows():
        if len(uf.groups([k for k in range(n) if uf.find(k) == uf.find(g[0])])[0]) != len(g):
            continue  # já incorporada a um CI
        c = P[g].mean(axis=0)
        plong = max(pads[g[0]].w, pads[g[0]].h)
        nrm = np.array([-d[1], d[0]])
        for h in uf.groups():
            if len(h) > 2 or uf.find(h[0]) == uf.find(g[0]):
                continue
            o = P[h].mean(axis=0) - c
            if abs(float(o @ d)) <= length / 2 + 0.3 and plong * 0.8 < abs(float(o @ nrm)) <= 3.5 * plong + 1.0:
                uf.union(g[0], h[0])

    # Fase 6: pares de pads isolados (passivos com pads largos) e SOT-23 (par + 1 pad em frente)
    singles = [g[0] for g in uf.groups() if len(g) == 1]
    _pair_mutual(pads, P, singles, uf, max_factor=6.0)
    for g in [g for g in uf.groups() if len(g) == 1]:
        i = g[0]
        best = None
        for h in uf.groups():
            if len(h) != 2 or not _same_size(pads[h[0]], pads[i]):
                continue
            a, b = P[h[0]], P[h[1]]
            span = float(np.hypot(*(b - a)))
            mid = (a + b) / 2
            u = (b - a) / max(span, 1e-9)
            o = P[i] - mid
            along, perp = abs(float(o @ u)), abs(float(o @ np.array([-u[1], u[0]])))
            if along <= 0.3 * span and 0.5 * span <= perp <= 1.6 * span:
                if best is None or perp < best[0]:
                    best = (perp, h[0])
        if best:
            uf.union(best[1], i)
    return uf.groups()


def _pair_mutual(pads, P, idx, uf, max_factor):
    idx = list(idx)
    if len(idx) < 2:
        return
    Q = P[idx]
    nearest = {}
    for a, i in enumerate(idx):
        d = np.hypot(Q[:, 0] - Q[a, 0], Q[:, 1] - Q[a, 1])
        d[a] = np.inf
        for b in np.argsort(d):
            j = idx[int(b)]
            if not np.isfinite(d[b]):
                break
            if _same_size(pads[i], pads[j]):
                lim = max_factor * max(pads[i].w, pads[i].h)
                if d[b] <= lim:
                    nearest[i] = j
                break
    for i, j in nearest.items():
        if nearest.get(j) == i:
            uf.union(i, j)


# =========================================================================== #
# Estimativa geométrica — PTH
# =========================================================================== #
def _link_pth_by_pitch(holes, idx) -> list[list[int]]:
    """Agrupa furos SEM contorno de serigrafia: fileiras de passo padrão (2,54 / 2,0 / 2,5 mm;
    até 5,08 mm só para furos >= 1,3 mm — bornes) e DIP (duas fileiras paralelas)."""
    idx = list(idx)
    if not idx:
        return []
    P = np.array([(holes[i].x, holes[i].y) for i in idx])
    D = np.array([holes[i].diameter for i in idx])
    uf = _UF(len(idx))
    for a in range(len(idx)):
        lim = 5.2 if D[a] >= 1.25 else 2.75
        d = np.hypot(P[:, 0] - P[a, 0], P[:, 1] - P[a, 1])
        for b in np.nonzero((d <= lim) & (np.abs(D - D[a]) <= 0.06))[0]:
            if b != a:
                uf.union(a, int(b))
    rws = []
    for g in uf.groups():
        if len(g) >= 2:
            d, length, pitch, ok = _row_info(P[g])
            if ok:
                rws.append((g, d, length, pitch))
    for x in range(len(rws)):
        ga, da, la, pa = rws[x]
        for y in range(x + 1, len(rws)):
            gb, db, lb, pb = rws[y]
            if uf.find(ga[0]) == uf.find(gb[0]) or len(ga) != len(gb) or len(ga) < 2:
                continue
            if abs(abs(float(da @ db)) - 1) > 0.02 or abs(pa - pb) > 0.1 * pa:
                continue
            o = P[gb].mean(axis=0) - P[ga].mean(axis=0)
            along = abs(float(o @ da))
            perp = abs(float(o @ np.array([-da[1], da[0]])))
            if along <= 0.3 * pa and 5.0 <= perp <= 16.0:
                uf.union(ga[0], gb[0])
    return [[idx[k] for k in g] for g in uf.groups()]


# =========================================================================== #
# Serigrafia: contornos fechados do corpo dos componentes
# =========================================================================== #
def _silk_bodies(silk: GerberLayer | None, max_dim: float, board_area: float = 0.0) -> list[np.ndarray]:
    if silk is None or not silk.tracks:
        return []
    loops = []
    for lp in chain_loops(silk):
        w = float(np.ptp(lp[:, 0]))
        h = float(np.ptp(lp[:, 1]))
        if board_area and w * h > 0.08 * board_area:
            continue  # contorno de bloco/placa, não de um componente
        if 1.2 <= max(w, h) <= max_dim and min(w, h) >= 0.5:
            loops.append(lp)
    return loops


def _merge_by_silk(groups: list[list[int]], P: np.ndarray, loops, allow) -> list[list[int]]:
    """Une grupos cujo centro está dentro do MESMO contorno de serigrafia (o menor que os contém)."""
    if not loops or len(groups) < 2:
        return groups
    from matplotlib.path import Path

    paths = [(Path(lp), float(np.ptp(lp[:, 0]) * np.ptp(lp[:, 1]))) for lp in loops]
    owner: dict[int, int] = {}
    for gi, g in enumerate(groups):
        if not allow(g):
            continue
        pts = P[g]
        best = None
        for li, (path, area) in enumerate(paths):
            if all(path.contains_point(tuple(p), radius=0.25) or path.contains_point(tuple(p), radius=-0.25)
                   for p in pts):
                if best is None or area < best[1]:
                    best = (li, area)
        if best is not None:
            owner[gi] = best[0]
    merged: dict[int, list[int]] = {}
    out = []
    for gi, g in enumerate(groups):
        if gi in owner:
            merged.setdefault(owner[gi], []).extend(g)
        else:
            out.append(g)
    return out + list(merged.values())


# =========================================================================== #
# Montagem dos componentes
# =========================================================================== #
def _smd_components(pads: list[_Pad], side: str, silk, board_area: float = 0.0) -> list[Component]:
    groups = _cluster_smd(pads)
    if not pads:
        return []
    P = np.array([(p.x, p.y) for p in pads])
    loops = _silk_bodies(silk, 25.0, board_area)
    # serigrafia não une CIs já formados (evita juntar blocos inteiros)
    groups = _merge_by_silk(groups, P, loops, allow=lambda g: len(g) <= 4)
    comps = []
    for g in groups:
        ps = [pads[i] for i in g]
        bb = _bbox([(p.x, p.y, p.w, p.h) for p in ps])
        big = max(ps, key=lambda p: p.w * p.h)
        sig = f"SMD-{len(g)}-{round(min(big.w, big.h), 1)}x{round(max(big.w, big.h), 1)}"
        comps.append(Component("SMD", side, (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2, bb, len(g), signature=sig))
    return comps


def _is_uniform_grid(P: np.ndarray) -> bool:
    """Pontos formam uma matriz completa e regular (mesmo passo) nos eixos principais?"""
    if len(P) < 2:
        return True
    d, _, _, _ = _row_info(P)
    n = np.array([-d[1], d[0]])
    u = np.round((P @ d) / 0.05) * 0.05
    v = np.round((P @ n) / 0.05) * 0.05
    uu, vv = np.unique(u), np.unique(v)
    if len(uu) * len(vv) != len(P):
        return False
    steps = np.concatenate([np.diff(uu), np.diff(vv)])
    return bool(len(steps) == 0 or (steps.max() - steps.min()) <= 0.01 * steps.mean() + 0.03)


def _merge_continuous_grids(holes, groups):
    """Une contornos vizinhos cujos furos continuam a MESMA matriz de passo padrão
    (ex.: barra 2x3 desenhada como 3 retângulos de 2 pinos). Conectores encostados com
    espaçamento diferente do passo (ou fileiras a 5 mm) continuam separados."""
    changed = True
    groups = [(list(g), s) for g, s in groups]
    while changed:
        changed = False
        for a in range(len(groups)):
            for b in range(a + 1, len(groups)):
                ga, gb = groups[a][0], groups[b][0]
                if abs(holes[ga[0]].diameter - holes[gb[0]].diameter) > 0.06:
                    continue
                Pa = np.array([(holes[i].x, holes[i].y) for i in ga])
                Pb = np.array([(holes[i].x, holes[i].y) for i in gb])
                dmin = float(np.min(np.hypot(Pa[:, None, 0] - Pb[None, :, 0], Pa[:, None, 1] - Pb[None, :, 1])))
                if dmin > 2.75 or not _is_uniform_grid(np.vstack([Pa, Pb])):
                    continue
                if len(Pa) > 1:   # passo = menor distância entre furos do próprio grupo
                    dd = np.hypot(Pa[:, None, 0] - Pa[None, :, 0], Pa[:, None, 1] - Pa[None, :, 1])
                    pitch_a = float(dd[dd > 1e-6].min())
                else:
                    pitch_a = dmin
                if abs(dmin - pitch_a) > 0.01 * pitch_a + 0.03:
                    continue
                groups[a] = (ga + gb, groups[a][1])
                del groups[b]
                changed = True
                break
            if changed:
                break
    return groups


def silk_faces(silk: GerberLayer | None, board_area: float = 0.0):
    """Regiões FECHADAS desenhadas pela serigrafia (contorno do corpo de cada componente).

    Os traços são engrossados pela própria largura e unidos; os "buracos" da área resultante são
    as regiões fechadas. Isso funciona mesmo com contornos vizinhos que compartilham um lado
    (conectores empilhados) e tolera pequenas falhas nos cantos.
    """
    if silk is None or not silk.tracks or not HAS_SHAPELY:
        return []
    from shapely import LineString, unary_union

    bufs = []
    for t in silk.tracks:
        if math.hypot(t.x2 - t.x1, t.y2 - t.y1) < 1e-6:
            continue
        bufs.append(LineString([(t.x1, t.y1), (t.x2, t.y2)]).buffer(max(t.width / 2, 0.05) + 0.03,
                                                                     quad_segs=2))
    if not bufs:
        return []
    area = unary_union(bufs)
    polys = getattr(area, "geoms", [area])
    faces = []
    from shapely import Polygon

    for poly in polys:
        for ring in getattr(poly, "interiors", []):
            f = Polygon(ring)
            minx, miny, maxx, maxy = f.bounds
            if f.area < 0.8 or max(maxx - minx, maxy - miny) > 80:
                continue
            if board_area and f.area > 0.08 * board_area:
                continue
            faces.append(f)
    return faces


def _pth_components(holes, side_of, silk_top, silk_bot, isolated: list | None = None,
                    board_area: float = 0.0, paste: dict | None = None) -> list[Component]:
    """PTH: o contorno da serigrafia define quais furos formam cada componente.

    1. Furos dentro do mesmo contorno fechado (o menor que os contém) = 1 componente.
       Contornos com 1 só furo ou com pads SMD (corpo de componente SMD) não geram PTH:
       os furos ali são vias de passagem / pinos de fixação.
    2. Furos fora de contornos: fileiras de passo padrão (≥ 3 furos) e DIP.
    3. Pares de furos sem contorno: só contam se houver o corpo de um axial na serigrafia entre
       eles; caso contrário são tratados como furos de passagem (não contam).
    """
    if not holes:
        return []
    faces = [(f, "TOP") for f in silk_faces(silk_top, board_area)]
    faces += [(f, "BOT") for f in silk_faces(silk_bot, board_area)]
    paste = paste or {}
    owner: dict[int, int] = {}
    if faces:
        tree = shapely.STRtree([f for f, _ in faces])
        for i, h in enumerate(holes):
            cand = tree.query(shapely.Point(h.x, h.y), predicate="within")
            if len(cand):
                owner[i] = int(min(cand, key=lambda k: faces[int(k)][0].area))
    comps: list[Component] = []
    by_face: dict[int, list[int]] = {}
    for i, f in owner.items():
        by_face.setdefault(f, []).append(i)
    ignored: list[int] = []
    released: list[int] = []
    groups: list[tuple[list[int], str | None]] = []
    for fi, g in by_face.items():
        face, fside = faces[fi]
        smd_inside = 0
        for side_pads in paste.values():
            if side_pads:
                xs = np.array([q.x for q in side_pads])
                ys = np.array([q.y for q in side_pads])
                smd_inside += int(shapely.contains_xy(face, xs, ys).sum())
        if smd_inside >= len(g):
            ignored += g            # contorno de componente SMD: furos são vias/pinos de fixação
        elif len(g) < 2:
            released.append(g[0])   # contorno de 1 furo (ex.: octógono por pino) -> agrupa por passo
        else:
            groups.append((g, fside))
    groups = _merge_continuous_grids(holes, groups)
    # furos fora de qualquer contorno
    free = [i for i in range(len(holes)) if i not in owner] + released
    empty_faces = [faces[k][0] for k in range(len(faces)) if k not in by_face]
    paired: set[int] = set()
    for f in empty_faces:   # corpo de axial entre dois furos
        lp = np.array(f.exterior.coords)
        d, _, _, _ = _row_info(lp)
        c = np.array([f.centroid.x, f.centroid.y])
        w = float(np.ptp(lp @ np.array([-d[1], d[0]])))
        half = float(np.ptp(lp @ d)) / 2
        if half * 2 > 30 or half * 2 < 2.4 * max(w, 0.1):
            continue
        ends = {1: None, -1: None}
        for i in free:
            if i in paired:
                continue
            o = np.array([holes[i].x, holes[i].y]) - c
            along, perp = float(o @ d), abs(float(o @ np.array([-d[1], d[0]])))
            if perp <= max(1.0, w / 2) and half - 0.5 <= abs(along) <= half + 5.0:
                sg = 1 if along > 0 else -1
                if ends[sg] is None or abs(along) < abs(float((np.array([holes[ends[sg]].x,
                                                                          holes[ends[sg]].y]) - c) @ d)):
                    ends[sg] = i
        a, b = ends[1], ends[-1]
        if a is not None and b is not None and abs(holes[a].diameter - holes[b].diameter) <= 0.06:
            groups.append(([a, b], None))
            paired |= {a, b}
    for g in _link_pth_by_pitch(holes, [i for i in free if i not in paired]):
        if len(g) >= 3:
            groups.append((g, None))
        else:
            ignored += g
    for g, fside in groups:
        hs = [holes[i] for i in g]
        bb = _bbox([(h.x, h.y, h.diameter * 1.9, h.diameter * 1.9) for h in hs])
        cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
        P = np.array([(h.x, h.y) for h in hs])
        _, length, pitch, _ = _row_info(P)
        side = side_of(cx, cy, fside)
        sig = f"PTH-{len(g)}-{round(hs[0].diameter, 1)}-{round(pitch, 1)}"
        comps.append(Component("PTH", side, cx, cy, bb, len(g), signature=sig))
    if isolated is not None:
        isolated += [holes[i] for i in ignored]
    return comps


_RE_THT = re.compile(r"(^|[^a-z])(dip|pdip|to-?92|to-?220|to-?247|to-?3p|axial|radial|tht|pth|"
                     r"pinheader|pin_header|pin-header|hdr|header|terminal|borne|relay|rele|cp_radial|"
                     r"c_disc|sip|jack|banana|trimpot|potentiometer|hc-?49u?\b|_th\b|-th\b)", re.I)
_RE_SMD = re.compile(r"(0201|0402|0603|0805|1206|1210|1812|2010|2512|sot|soic|so-?\d|ssop|tssop|msop|"
                     r"qfn|qfp|lqfp|tqfp|dfn|bga|lga|sod|\bsma\b|\bsmb\b|\bsmc\b|melf|smd|smt|plcc|dpak|"
                     r"d2pak|powerpak|son\b|wson|vson|uson|tant|case_[a-e]\b)", re.I)


def _from_pnp(placements: list[Placement], paste_pads: dict, pth_holes) -> list[Component]:
    comps: list[Component] = []
    HP = np.array([(h.x, h.y) for h in pth_holes]).reshape(-1, 2)
    kinds = []
    for p in placements:
        fp = f"{p.footprint} {p.value}"
        if _RE_THT.search(p.footprint or ""):
            kind = "PTH"
        elif _RE_SMD.search(fp):
            kind = "SMD"
        else:  # decide pela geometria mais próxima do centro
            pads = paste_pads.get(p.side, [])
            d_pad = min((math.hypot(q.x - p.x, q.y - p.y) for q in pads), default=math.inf)
            d_hole = float(np.min(np.hypot(HP[:, 0] - p.x, HP[:, 1] - p.y))) if len(HP) else math.inf
            kind = "PTH" if d_hole < d_pad and d_hole < 15 else "SMD"
        kinds.append(kind)
    # terminais: cada pad/furo é atribuído ao componente mais próximo do mesmo tipo/lado
    term = [0] * len(placements)
    boxes = [[p.x - 0.5, p.y - 0.5, p.x + 0.5, p.y + 0.5] for p in placements]

    def assign(points, cand, limit):
        if not cand:
            return
        C = np.array([(placements[i].x, placements[i].y) for i in cand])
        for (x, y, w, h) in points:
            d = np.hypot(C[:, 0] - x, C[:, 1] - y)
            k = int(np.argmin(d))
            if d[k] <= limit:
                i = cand[k]
                term[i] += 1
                b = boxes[i]
                b[0], b[1] = min(b[0], x - w / 2), min(b[1], y - h / 2)
                b[2], b[3] = max(b[2], x + w / 2), max(b[3], y + h / 2)

    for side in ("TOP", "BOT"):
        cand = [i for i, p in enumerate(placements) if kinds[i] == "SMD" and p.side == side]
        assign([(q.x, q.y, q.w, q.h) for q in paste_pads.get(side, [])], cand, 15.0)
    cand = [i for i in range(len(placements)) if kinds[i] == "PTH"]
    assign([(h.x, h.y, h.diameter * 1.9, h.diameter * 1.9) for h in pth_holes], cand, 40.0)
    for i, p in enumerate(placements):
        t = term[i] or 2
        sig = f"{p.value}|{p.footprint}" if (p.value or p.footprint) else f"{kinds[i]}-{t}"
        comps.append(Component(kinds[i], p.side, p.x, p.y, tuple(boxes[i]), t, p.designator, p.footprint,
                               p.value, sig))
    return comps


def count_components(board, pth_top: bool = True, pth_bot: bool = False) -> ComponentCount:
    """Conta componentes SMD (por lado) e PTH. ``board.holes`` já deve estar resolvido."""
    paste = {"TOP": _pads(board.paste_top), "BOT": _pads(board.paste_bot)}
    pth_holes = [h for h in board.holes if h.plated and not h.is_via]
    res = ComponentCount()
    placements = getattr(board, "placements", [])
    if placements:
        res.components = _from_pnp(placements, paste, pth_holes)
        res.exact = True
        res.source = "Pick & Place (" + ", ".join(getattr(board, "pnp_names", [])) + ")"
        res.excluded = list(getattr(board, "pnp_excluded", []))
        side_pth = {c.side for c in res.components if c.kind == "PTH"}
        if "BOT" in side_pth and not pth_bot:
            res.warnings.append("Pick & Place indica componentes PTH no lado BOT: confira o checkbox "
                                "'PTH no Bot'.")
        return res

    res.source = "Estimativa geométrica (pads Paste + furos PTH + contornos da serigrafia)"
    for side, silk in (("TOP", board.silk_top), ("BOT", board.silk_bot)):
        res.components += _smd_components(paste[side], side, silk, board.width * board.height)

    def side_of(x, y, face_side=None):
        if pth_top and not pth_bot:
            return "TOP"
        if pth_bot and not pth_top:
            return "BOT"
        return face_side or "TOP"   # PTH nos dois lados: lado da serigrafia do contorno

    if pth_top or pth_bot:
        isolated: list = []
        res.components += _pth_components(pth_holes, side_of, board.silk_top, board.silk_bot, isolated,
                                          board.width * board.height, paste)
        if isolated:
            big = sum(1 for h in isolated if h.diameter >= 2.5)
            res.warnings.append(
                f"{len(isolated)} furo(s) PTH sem componente (vias de passagem, fixação ou pontos de teste) "
                "não contado(s)" + (f", {big} com Ø ≥ 2,5 mm" if big else "")
                + ": confira no desenho de montagem.")
    if not HAS_SHAPELY:
        res.warnings.append("Biblioteca 'shapely' não instalada: contornos da serigrafia não usados na "
                            "contagem PTH. Execute: pip install -r requirements.txt")
    if res.components:
        res.warnings.append("Contagem de componentes ESTIMADA pela geometria. Para contagem exata, "
                            "inclua o arquivo Pick & Place (centroide) do cliente.")
    return res
