"""Renderização dos desenhos técnicos (PNG) com Matplotlib (backend Agg).

As mesmas imagens alimentam a interface (QPixmap) e o PDF, garantindo que o
orçamentista veja exatamente o que vai para o cliente/comercial.

Vistas:
* ``overview``    – Vista geral (cobre top/bot, silk, furos, contorno);
* ``top`` / ``bot`` – Montagem (pads SMD + furos PTH); BOT espelhado (visão inferior);
* ``dimdrill``    – Dimensional + Furação num único desenho (cotas conforme formato
  da placa e tabela de brocas PTH/via/NPTH);
* ``package``     – Desenho de embalagem para cotação com fabricante de caixa.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass

import matplotlib

matplotlib.use("Agg")

from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402
from matplotlib.collections import LineCollection, PolyCollection  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from core.analyzer import AnalysisResult, BoardData, fmt_mm  # noqa: E402
from core.parsers import GerberLayer, circle_poly  # noqa: E402

BRAND_BLUE = "#000099"
COLORS = {
    "outline": "#111111",
    "board": "#F3F6EE",
    "copper_top": "#C8742E",
    "copper_bot": "#2F6DB5",
    "inner": "#9AA5B1",
    "silk": "#4D4D4D",
    "silk_faint": "#A8A8A8",
    "copper_faint": "#E4E4E4",
    "smd": "#E8590C",        # laranja: pads SMD
    "pth": "#0B8F3A",        # verde: furos PTH de componentes
    "via": "#7B8CA8",
    "npth": "#C1121F",       # vermelho: furos mecânicos
    "warn_bg": "#FFF3CD",
    "warn_fg": "#8A5A00",
    "alert_bg": "#FDE2E1",
    "alert_fg": "#B00020",
}

DRILL_MARKERS = ["o", "s", "^", "D", "v", "p", "h", "*", "X", "P", "<", ">", "8", "d"]


@dataclass
class _Canvas:
    fig: Figure
    ax: object
    pts_per_mm: float


def _new_canvas(bbox, title: str, subtitle: str = "", pad_frac: float = 0.06,
                extra_pad: float = 0.0, width_in: float = 10.0, bottom_in: float = 0.7) -> _Canvas:
    xmin, ymin, xmax, ymax = bbox
    xr = max(xmax - xmin, 1.0)
    yr = max(ymax - ymin, 1.0)
    pad = max(xr, yr) * pad_frac + extra_pad
    xr_p, yr_p = xr + 2 * pad, yr + 2 * pad
    left_in = right_in = 0.35
    top_in = 0.95 if subtitle else 0.7
    ax_w = width_in - left_in - right_in
    ax_h = min(max(ax_w * yr_p / xr_p, 2.6), 9.0)
    fig_h = ax_h + top_in + bottom_in
    fig = Figure(figsize=(width_in, fig_h), facecolor="white")
    FigureCanvasAgg(fig)
    ax = fig.add_axes([left_in / width_in, bottom_in / fig_h, ax_w / width_in, ax_h / fig_h])
    ax.set_xlim(xmin - pad, xmax + pad)
    ax.set_ylim(ymin - pad, ymax + pad)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")
    fig.text(0.5, 1 - 0.32 / fig_h, title, ha="center", va="center", fontsize=15,
             fontweight="bold", color=BRAND_BLUE)
    if subtitle:
        fig.text(0.5, 1 - 0.66 / fig_h, subtitle, ha="center", va="center", fontsize=10, color="#333333")
    ppm = min(ax_w * 72 / xr_p, ax_h * 72 / yr_p)
    return _Canvas(fig, ax, ppm)


def _banner(c: _Canvas, text: str, alert: bool = False, y_in: float = 0.28) -> None:
    fig_h = c.fig.get_figheight()
    c.fig.text(0.5, y_in / fig_h, text, ha="center", va="center", fontsize=12, fontweight="bold",
               color=COLORS["alert_fg"] if alert else COLORS["warn_fg"],
               bbox=dict(boxstyle="round,pad=0.45", fc=COLORS["alert_bg"] if alert else COLORS["warn_bg"],
                         ec=COLORS["alert_fg"] if alert else COLORS["warn_fg"], lw=1.2))


def _to_png(fig: Figure, dpi: int) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, facecolor="white")
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Primitivas de desenho
# --------------------------------------------------------------------------- #
def _draw_layer(c: _Canvas, layer: GerberLayer | None, color: str, alpha: float = 1.0,
                zorder: int = 2, min_lw: float = 0.25, pads: bool = True, tracks: bool = True) -> None:
    if layer is None:
        return
    if pads:
        verts = []
        for f in layer.flashes:
            for p in f.aperture.polys:
                verts.append(p + (f.x, f.y))
        verts += list(layer.regions)
        if verts:
            c.ax.add_collection(PolyCollection(verts, facecolors=color, edgecolors="none",
                                               alpha=alpha, zorder=zorder))
    if tracks and layer.tracks:
        segs = [((t.x1, t.y1), (t.x2, t.y2)) for t in layer.tracks]
        lws = [max(t.width * c.pts_per_mm, min_lw) for t in layer.tracks]
        c.ax.add_collection(LineCollection(segs, colors=color, linewidths=lws, alpha=alpha,
                                           capstyle="round", zorder=zorder))


def _draw_outline(c: _Canvas, board: BoardData, color: str = COLORS["outline"], lw: float = 1.4,
                  fill: bool = True) -> None:
    shape = board.shape
    if fill and shape is not None and shape.polygon is not None:
        c.ax.add_collection(PolyCollection([shape.polygon], facecolors=COLORS["board"], edgecolors="none",
                                           zorder=0))
        if shape.cutouts:
            c.ax.add_collection(PolyCollection(shape.cutouts, facecolors="white", edgecolors="none",
                                               zorder=0.5))
    if board.outline is not None and board.outline.tracks:
        segs = [((t.x1, t.y1), (t.x2, t.y2)) for t in board.outline.tracks]
        c.ax.add_collection(LineCollection(segs, colors=color, linewidths=lw, zorder=6,
                                           capstyle="round"))
        regions = board.outline.regions
        if regions:
            c.ax.add_collection(PolyCollection(regions, facecolors="none", edgecolors=color,
                                               linewidths=lw, zorder=6))
    else:
        x0, y0, x1, y1 = board.bbox
        c.ax.plot([x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0], color=color, lw=lw, ls="--", zorder=6)


def _circles(holes, scale: float = 1.0):
    return [circle_poly(h.x, h.y, h.diameter * scale / 2, 20) for h in holes]


# --------------------------------------------------------------------------- #
# Vistas
# --------------------------------------------------------------------------- #
def render_overview(board: BoardData, result: AnalysisResult, dpi: int = 130) -> bytes:
    c = _new_canvas(board.bbox, "Vista Geral da Placa",
                    f"{result.layers} layers  |  {result.board_text}  |  área {fmt_mm(result.area_cm2)} cm²")
    _draw_outline(c, board)
    for inner in board.inner:
        _draw_layer(c, inner, COLORS["inner"], alpha=0.18, zorder=1, pads=True)
    _draw_layer(c, board.copper_bot, COLORS["copper_bot"], alpha=0.45, zorder=2)
    _draw_layer(c, board.copper_top, COLORS["copper_top"], alpha=0.65, zorder=3)
    _draw_layer(c, board.silk_top, COLORS["silk"], alpha=0.9, zorder=4, min_lw=0.3)
    holes = board.holes
    if holes:
        c.ax.add_collection(PolyCollection(_circles(holes), facecolors="white",
                                           edgecolors="#333333", linewidths=0.3, zorder=5))
    handles = [Patch(color=COLORS["copper_top"], alpha=0.65, label="Cobre TOP"),
               Patch(color=COLORS["copper_bot"], alpha=0.45, label="Cobre BOT"),
               Patch(color=COLORS["silk"], label="Silkscreen TOP"),
               Line2D([], [], marker="o", ls="", mfc="white", mec="#333", label="Furos")]
    if board.inner:
        handles.insert(2, Patch(color=COLORS["inner"], alpha=0.3, label="Inner layers"))
    c.fig.legend(handles=handles, loc="lower center", ncol=len(handles), frameon=False, fontsize=9)
    return _to_png(c.fig, dpi)


def render_assembly(board: BoardData, result: AnalysisResult, side: str, dpi: int = 130) -> bytes | None:
    """Desenho de montagem. Retorna ``None`` se o lado não tiver montagem."""
    status = result.top if side == "TOP" else result.bot
    if not status.has_view:
        return None
    is_bot = side == "BOT"
    comps = [k for k in (result.components.components if result.components else []) if k.side == side]
    n_smd = sum(1 for k in comps if k.kind == "SMD")
    n_pth = sum(1 for k in comps if k.kind == "PTH")
    sub = (f"Componentes SMD: {n_smd} ({status.smd_pads} pads)  |  Componentes PTH: {n_pth} "
           f"({status.pth_holes} furos)" + ("  |  VISTA PELO LADO INFERIOR (ESPELHADA)" if is_bot else ""))
    c = _new_canvas(board.bbox, f"Montagem Lado {side}", sub,
                    bottom_in=1.05 if status.is_partial else 0.7)
    _draw_outline(c, board)
    copper = board.copper_bot if is_bot else board.copper_top
    silk = board.silk_bot if is_bot else board.silk_top
    paste = board.paste_bot if is_bot else board.paste_top
    _draw_layer(c, copper, COLORS["copper_faint"], zorder=1, pads=True)
    _draw_layer(c, silk, COLORS["silk_faint"], zorder=2, min_lw=0.3)
    handles = []
    if status.has_smd:
        if paste is not None:
            _draw_layer(c, paste, COLORS["smd"], zorder=4, tracks=False)
        handles.append(Patch(color=COLORS["smd"], label=f"Pads SMD ({status.smd_pads})"))
    if status.has_pth:
        plated = [h for h in board.holes if h.plated and not h.is_via]
        boxes = [k.bbox for k in (result.components.components if result.components else []) if k.kind == "PTH"]

        def in_comp(h):
            return any(b[0] <= h.x <= b[2] and b[1] <= h.y <= b[3] for b in boxes)

        comp = [h for h in plated if in_comp(h)] if boxes else plated
        loose = [h for h in plated if boxes and not in_comp(h)]
        c.ax.add_collection(PolyCollection(_circles(comp, 1.9), facecolors=COLORS["pth"],
                                           edgecolors="none", zorder=5))
        c.ax.add_collection(PolyCollection(_circles(comp), facecolors="white",
                                           edgecolors="none", zorder=6))
        handles.append(Line2D([], [], marker="o", ls="", ms=9, mfc="white", mec=COLORS["pth"], mew=3,
                              label=f"Furos PTH de componente ({len(comp)})"))
        if loose:
            c.ax.add_collection(PolyCollection(_circles(loose, 1.4), facecolors=COLORS["via"],
                                               edgecolors="none", zorder=5, alpha=0.8))
            handles.append(Line2D([], [], marker="o", ls="", ms=6, mfc=COLORS["via"], mec=COLORS["via"],
                                  label=f"Furo de passagem / sem componente ({len(loose)})"))
    npth = [h for h in board.holes if not h.plated]
    if npth:
        c.ax.add_collection(PolyCollection(_circles(npth), facecolors="none",
                                           edgecolors=COLORS["npth"], linewidths=0.8, zorder=6))
        handles.append(Line2D([], [], marker="o", ls="", mfc="none", mec=COLORS["npth"],
                              label=f"NPTH ({len(npth)})"))
    # caixas dos componentes detectados (conferência visual da contagem)
    if comps:
        from matplotlib.patches import Rectangle as _R

        exact = bool(result.components and result.components.exact)
        for k in comps:
            x0, y0, x1, y1 = k.bbox
            m = 0.25
            col = COLORS["smd"] if k.kind == "SMD" else COLORS["pth"]
            c.ax.add_patch(_R((x0 - m, y0 - m), x1 - x0 + 2 * m, y1 - y0 + 2 * m, fill=False, ec=col,
                              lw=0.7, ls="--", zorder=7, alpha=0.9))
            if exact and k.designator:
                c.ax.text(x1 + m, y1 + m, k.designator, fontsize=5.5, color="#1f1f1f", zorder=8,
                          ha="left", va="bottom")
        handles.append(Patch(fill=False, ec="#555", ls="--",
                             label="Componente " + ("(Pick & Place)" if exact else "(estimado)")))
    if is_bot:
        c.ax.invert_xaxis()
    c.fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 0.0), ncol=max(1, len(handles)),
                 frameon=False, fontsize=9)
    if status.is_partial:
        _banner(c, status.message, alert=False, y_in=0.72)
    return _to_png(c.fig, dpi)


def _draw_dimension(ax, dim, bbox, off: float, poly) -> None:
    """Desenha uma cota (linhas de chamada, seta dupla e texto) fora da placa."""
    x0, y0, x1, y1 = bbox
    e = dim.edge
    col = BRAND_BLUE
    gap = off * 0.15
    dist = off * (1.0 + 1.15 * dim.level)
    arrow = dict(arrowstyle="<|-|>", color=col, lw=1.1, shrinkA=0, shrinkB=0, mutation_scale=12)
    txt = f"{fmt_mm(dim.value)} mm"
    label = f"{dim.label} = {txt}" if dim.label in ("X", "Y") else f"{txt}  ({dim.label})"
    tstyle = dict(fontsize=11, fontweight="bold", color=col, bbox=dict(fc="white", ec="none", pad=1.5),
                  zorder=9)
    if dim.kind == "H":
        xa, xb = sorted((e.x1, e.x2))
        ye = e.y1
        yd = (y0 - dist) if dim.side != "top" else (y1 + dist)
        sgn = -1 if dim.side != "top" else 1
        for xx in (xa, xb):
            ax.plot([xx, xx], [ye + sgn * gap, yd + sgn * off * 0.25], color=col, lw=0.7, zorder=8)
        ax.annotate("", xy=(xa, yd), xytext=(xb, yd), arrowprops=arrow, zorder=8)
        ax.text((xa + xb) / 2, yd + sgn * off * 0.12, label, ha="center",
                va="top" if sgn < 0 else "bottom", **tstyle)
    elif dim.kind == "V":
        ya, yb = sorted((e.y1, e.y2))
        xe = e.x1
        xd = (x0 - dist) if dim.side != "right" else (x1 + dist)
        sgn = -1 if dim.side != "right" else 1
        for yy in (ya, yb):
            ax.plot([xe + sgn * gap, xd + sgn * off * 0.25], [yy, yy], color=col, lw=0.7, zorder=8)
        ax.annotate("", xy=(xd, ya), xytext=(xd, yb), arrowprops=arrow, zorder=8)
        ax.text(xd + sgn * off * 0.12, (ya + yb) / 2, label, ha="right" if sgn < 0 else "left",
                va="center", rotation=90, **tstyle)
    else:  # cota alinhada a um lado inclinado
        from matplotlib.path import Path

        dx, dy = e.x2 - e.x1, e.y2 - e.y1
        ln = max(math.hypot(dx, dy), 1e-9)
        nx, ny = -dy / ln, dx / ln
        mx, my = (e.x1 + e.x2) / 2, (e.y1 + e.y2) / 2
        if poly is not None and Path(poly).contains_point((mx + nx * off * 0.1, my + ny * off * 0.1)):
            nx, ny = -nx, -ny
        d = off * 0.8
        p1 = (e.x1 + nx * d, e.y1 + ny * d)
        p2 = (e.x2 + nx * d, e.y2 + ny * d)
        for (px, py), (qx, qy) in (((e.x1, e.y1), p1), ((e.x2, e.y2), p2)):
            ax.plot([px + nx * gap, qx + nx * off * 0.2], [py + ny * gap, qy + ny * off * 0.2],
                    color=col, lw=0.7, zorder=8)
        ax.annotate("", xy=p1, xytext=p2, arrowprops=arrow, zorder=8)
        ang = math.degrees(math.atan2(dy, dx))
        if ang > 90:
            ang -= 180
        elif ang < -90:
            ang += 180
        ax.text((p1[0] + p2[0]) / 2 + nx * off * 0.25, (p1[1] + p2[1]) / 2 + ny * off * 0.25, label,
                ha="center", va="center", rotation=ang, rotation_mode="anchor", **tstyle)


ALTIUM_COLUMNS = ["Symbol", "Count", "Hole Size", "Plated", "Hole Type", "Drill Layer Pair"]
ALTIUM_COL_W = [0.10, 0.10, 0.26, 0.11, 0.13, 0.30]


def _altium_drill_table(ax, groups, color) -> None:
    """Tabela de furação no layout da "Drill Table" do Altium Designer:
    Symbol | Count | Hole Size | Plated | Hole Type | Drill Layer Pair, com linha de total."""
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    n = len(groups) + 2
    rh = 1.0 / n
    xs = [0.0]
    for w in ALTIUM_COL_W:
        xs.append(xs[-1] + w)
    line = dict(color="#000000", lw=0.8, clip_on=False)

    def row_y(i):  # topo da linha i
        return 1.0 - i * rh

    for i in range(n + 1):
        ax.plot([0, 1], [row_y(i), row_y(i)], **line)
    for x in xs:
        ax.plot([x, x], [0, 1], **line)
    for j, name in enumerate(ALTIUM_COLUMNS):
        ax.text((xs[j] + xs[j + 1]) / 2, row_y(0) - rh / 2, name, ha="center", va="center",
                fontsize=9, fontweight="bold")
    for i, g in enumerate(groups, start=1):
        yc = row_y(i) - rh / 2
        marker = DRILL_MARKERS[(i - 1) % len(DRILL_MARKERS)]
        filled = g.kind != "NPTH"
        ax.scatter([(xs[0] + xs[1]) / 2], [yc], s=55, marker=marker, c=color[g.kind] if filled else "none",
                   edgecolors=color[g.kind], linewidths=1.0, clip_on=False, zorder=5)
        values = ["", str(g.count), g.hole_size_text, g.plated_text, g.hole_type_text,
                  "Top Layer - Bottom Layer"]
        for j, v in enumerate(values):
            if v:
                ax.text((xs[j] + xs[j + 1]) / 2, yc, v, ha="center", va="center", fontsize=9)
    yc = row_y(n - 1) - rh / 2
    ax.text((xs[1] + xs[2]) / 2, yc, str(sum(g.count for g in groups)), ha="center", va="center",
            fontsize=9, fontweight="bold")
    ax.text((xs[2] + xs[3]) / 2, yc, "Total", ha="center", va="center", fontsize=9, fontweight="bold")


def render_dim_drill(board: BoardData, result: AnalysisResult, dpi: int = 130) -> bytes:
    """Desenho único: Dimensional (cotas conforme formato) + Mapa de Furação + tabela de brocas."""
    x0, y0, x1, y1 = board.bbox
    span = max(x1 - x0, y1 - y0, 1.0)
    off = span * 0.085
    shape = board.shape
    dims = shape.dimensions if shape is not None else []
    levels = max((d.level for d in dims), default=0)
    rule = ("Formato retangular/quadrado: cotas pelos lados" if shape is not None and shape.is_rectangular
            else "Formato irregular: cotas pelos lados de maior comprimento")
    if not board.bbox_from_outline:
        rule = "CONTORNO NÃO ENCONTRADO: envelope estimado"
    groups = result.drill_groups
    table_h = 0.27 * (len(groups) + 2) if groups else 0.0
    bottom = 0.45 + table_h + 0.35
    c = _new_canvas(board.bbox, "Dimensional e Furação",
                    f"Formato: {result.shape}  |  {rule}", extra_pad=off * (1.6 + 1.15 * levels),
                    bottom_in=bottom)
    ax = c.ax
    _draw_outline(c, board, lw=1.6)
    for d in dims:
        _draw_dimension(ax, d, board.bbox, off, shape.polygon if shape else None)

    color = {"PTH": COLORS["pth"], "VIA": COLORS["via"], "NPTH": COLORS["npth"]}
    handles = []
    for i, g in enumerate(groups):
        holes = [h for h in board.holes
                 if h.kind == g.kind and round(h.diameter, 3) == g.diameter and h.slot == g.slot]
        if not holes:
            continue
        marker = DRILL_MARKERS[i % len(DRILL_MARKERS)]
        filled = g.kind != "NPTH"
        size_pts = max(g.diameter * c.pts_per_mm, 4.5)
        ax.scatter([h.x for h in holes], [h.y for h in holes], s=size_pts ** 2, marker=marker, zorder=7,
                   c=color[g.kind] if filled else "none", edgecolors=color[g.kind], linewidths=0.9)
        handles.append(Line2D([], [], marker=marker, ls="", ms=8, mfc=color[g.kind] if filled else "none",
                              mec=color[g.kind], label=f"Ø{fmt_mm(g.diameter, 2)} {g.kind}"))

    fig = c.fig
    W, H = fig.get_figwidth(), fig.get_figheight()
    if handles:
        tax = fig.add_axes([0.06, 0.25 / H, 0.88, table_h / H])
        _altium_drill_table(tax, groups, color)
    else:
        _banner(c, "NENHUM FURO ENCONTRADO NOS ARQUIVOS DE DRILL", alert=True, y_in=0.6)
    fig.text(0.5, (bottom - 0.05) / H,
             f"Envelope: {result.board_text}   |   Área envelope: {fmt_mm(result.area_cm2)} cm²"
             + (f"   |   Área real: {fmt_mm(result.real_area_cm2)} cm²"
                if abs(result.real_area_cm2 - result.area_cm2) > 0.05 else "")
             + f"   |   PTH {result.pth_total} (comp. {result.pth_component}, vias {result.via_count})"
               f"   |   NPTH {result.npth_count}",
             ha="center", va="top", fontsize=9, color="#333333")
    _ = W
    return _to_png(fig, dpi)


def render_all(board: BoardData, result: AnalysisResult, dpi: int = 130,
               packaging=None) -> dict[str, bytes | None]:
    from render.package_renderer import render_packaging

    return {
        "overview": render_overview(board, result, dpi),
        "top": render_assembly(board, result, "TOP", dpi),
        "bot": render_assembly(board, result, "BOT", dpi),
        "dimdrill": render_dim_drill(board, result, dpi),
        "package": render_packaging(result, packaging, dpi),
    }
