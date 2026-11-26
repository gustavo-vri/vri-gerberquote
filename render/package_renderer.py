"""Desenho técnico de embalagem para cotação com fabricante de caixa.

Conteúdo (folha única, proporção A3/A4 paisagem):
* Croqui isométrico da caixa com medidas internas C x L x A;
* Vista superior com a PCI centralizada e folgas de proteção;
* Corte lateral com acolchoamento acima/abaixo e altura da placa montada;
* Planificação de referência (FEFCO 0201) ou nota da faca (FEFCO 0427);
* Quadro de especificação (carimbo) com tudo que o fabricante precisa para cotar.
"""
from __future__ import annotations

import io
import math
from datetime import datetime

import matplotlib

matplotlib.use("Agg")

from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.patches import Polygon, Rectangle  # noqa: E402

from core.analyzer import AnalysisResult, fmt_mm  # noqa: E402
from core.packaging import BOX_MODELS, GLUE_FLAP_0201, PackagingSpec, box_dimensions  # noqa: E402

BRAND = "#000099"
KRAFT_TOP = "#E9CDA0"
KRAFT_LEFT = "#D3AE76"
KRAFT_RIGHT = "#C29A5F"
LINE = "#3B2A14"
PCB = "#2E8B57"
CUSHION = "#DDE7F5"
ARROW = dict(arrowstyle="<|-|>", color=BRAND, lw=1.0, shrinkA=0, shrinkB=0, mutation_scale=11)
TXT = dict(fontsize=10, fontweight="bold", color=BRAND, ha="center", va="center",
           bbox=dict(fc="white", ec="none", pad=1.2))


def _dim(ax, p1, p2, text, offset=(0, 0), rotation=0):
    ax.annotate("", xy=p1, xytext=p2, arrowprops=ARROW)
    ax.text((p1[0] + p2[0]) / 2 + offset[0], (p1[1] + p2[1]) / 2 + offset[1], text, rotation=rotation,
            **TXT)


def _panel_title(ax, text):
    ax.set_title(text, fontsize=11.5, fontweight="bold", color=BRAND, loc="left", pad=6)


# --------------------------------------------------------------------------- #
# Croqui isométrico
# --------------------------------------------------------------------------- #
def _iso(x, y, z):
    c30, s30 = math.cos(math.radians(30)), math.sin(math.radians(30))
    return ((x - y) * c30, (x + y) * s30 + z)


def _draw_iso(ax, C, L, A, model):
    P = lambda x, y, z: _iso(x, y, z)  # noqa: E731
    faces = [
        ([P(0, 0, A), P(C, 0, A), P(C, L, A), P(0, L, A)], KRAFT_TOP),     # topo
        ([P(0, 0, 0), P(C, 0, 0), P(C, 0, A), P(0, 0, A)], KRAFT_RIGHT),   # face C (frente direita)
        ([P(0, 0, 0), P(0, L, 0), P(0, L, A), P(0, 0, A)], KRAFT_LEFT),    # face L (frente esquerda)
    ]
    for pts, col in faces:
        ax.add_patch(Polygon(pts, closed=True, fc=col, ec=LINE, lw=1.3))
    if model == "FEFCO 0201":
        # emenda das abas superiores ao centro + fita
        a, b = P(0, L / 2, A), P(C, L / 2, A)
        ax.plot([a[0], b[0]], [a[1], b[1]], color=LINE, lw=1.1)
        t = min(L * 0.08, 12)
        tape = [P(0, L / 2 - t, A), P(C, L / 2 - t, A), P(C, L / 2 + t, A), P(0, L / 2 + t, A)]
        ax.add_patch(Polygon(tape, closed=True, fc="#F3E3C3", ec="none", alpha=0.8))
        for (x, y) in ((0, L / 2 - t), (0, L / 2 + t)):  # fita descendo pela face
            p, q = P(x, y, A), P(x, y, A - min(A * 0.35, 25))
            ax.plot([p[0], q[0]], [p[1], q[1]], color=LINE, lw=0.5, alpha=0.6)
    else:
        # maleta: tampa articulada no fundo, aba de encaixe frontal
        h = min(A * 0.3, 15)
        a, b = P(0, 0, A - h), P(C, 0, A - h)
        ax.plot([a[0], b[0]], [a[1], b[1]], color=LINE, lw=1.0, ls="--")
        a, b = P(0, 0, A - h), P(0, L, A - h)
        ax.plot([a[0], b[0]], [a[1], b[1]], color=LINE, lw=1.0, ls="--")
    # cotas
    d = max(C, L, A) * 0.08
    p1, p2 = P(0, 0, 0), P(C, 0, 0)
    off = (d * 0.5, -d * 0.87)
    _dim(ax, (p1[0] + off[0], p1[1] + off[1]), (p2[0] + off[0], p2[1] + off[1]),
         f"C = {C:.0f} mm", (d * 0.35, -d * 0.6), rotation=30)
    p1, p2 = P(0, 0, 0), P(0, L, 0)
    off = (-d * 0.5, -d * 0.87)
    _dim(ax, (p1[0] + off[0], p1[1] + off[1]), (p2[0] + off[0], p2[1] + off[1]),
         f"L = {L:.0f} mm", (-d * 0.35, -d * 0.6), rotation=-30)
    p1, p2 = P(C, 0, 0), P(C, 0, A)
    _dim(ax, (p1[0] + d * 0.7, p1[1]), (p2[0] + d * 0.7, p2[1]), f"A = {A:.0f} mm", (d * 0.9, 0), rotation=90)
    xs = [P(x, y, z)[0] for x in (0, C) for y in (0, L) for z in (0, A)]
    ys = [P(x, y, z)[1] for x in (0, C) for y in (0, L) for z in (0, A)]
    pad = d * 2.4
    ax.set_xlim(min(xs) - pad, max(xs) + pad)
    ax.set_ylim(min(ys) - pad, max(ys) + pad * 0.6)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.text(0.5, 0.02, "Medidas INTERNAS (mm)", transform=ax.transAxes, ha="center", fontsize=9,
            color="#444")


# --------------------------------------------------------------------------- #
# Vista superior e corte
# --------------------------------------------------------------------------- #
def _hatch_box(ax, x, y, w, h):
    ax.add_patch(Rectangle((x, y), w, h, fc=CUSHION, ec="none", hatch="oo", alpha=0.9, lw=0))


def _draw_top(ax, b):
    C, L = b.length, b.width
    ax.add_patch(Rectangle((0, 0), C, L, fc=CUSHION, ec=LINE, lw=1.6, hatch="oo"))
    px, py = (C - b.pcb_x) / 2, (L - b.pcb_y) / 2
    ax.add_patch(Rectangle((px, py), b.pcb_x, b.pcb_y, fc=PCB, ec="#174D30", lw=1.0, alpha=0.9))
    ax.text(C / 2, L / 2, "PCI\n(saco antiestático ESD)", ha="center", va="center", color="white",
            fontsize=9, fontweight="bold")
    d = max(C, L) * 0.09
    _dim(ax, (0, -d), (C, -d), f"C = {C:.0f}", (0, -d * 0.45))
    _dim(ax, (-d, 0), (-d, L), f"L = {L:.0f}", (-d * 0.5, 0), rotation=90)
    ax.text(px / 2, L / 2, f"{fmt_mm(px, 0)}", ha="center", va="center", fontsize=8, color=BRAND,
            bbox=dict(fc="white", ec="none", pad=0.5),
            rotation=90, fontweight="bold")
    ax.text(C / 2, L - py / 2, f"{fmt_mm(py, 0)}", ha="center", va="center", fontsize=8, color=BRAND,
            bbox=dict(fc="white", ec="none", pad=0.5),
            fontweight="bold")
    ax.text(C / 2, L + d * 0.45, f"PCI {fmt_mm(b.pcb_x)} x {fmt_mm(b.pcb_y)} mm  |  folga {fmt_mm(b.margin_xy, 0)} "
            f"mm por lado (plástico bolha)", ha="center", fontsize=8.5, color="#333")
    ax.set_xlim(-d * 2.2, C + d * 0.8)
    ax.set_ylim(-d * 1.9, L + d * 1.1)
    ax.set_aspect("equal")
    ax.axis("off")


def _draw_section(ax, b):
    C, A, mz = b.length, b.height, b.margin_z
    ax.add_patch(Rectangle((0, 0), C, A, fc=CUSHION, ec=LINE, lw=1.6, hatch="oo"))
    px = (C - b.pcb_x) / 2
    pcb_t = min(1.6, b.assembly_height * 0.3)
    ax.add_patch(Rectangle((px, mz), b.pcb_x, pcb_t, fc=PCB, ec="#174D30", lw=0.8))
    comp = b.assembly_height - pcb_t
    if comp > 0.2:
        ax.add_patch(Rectangle((px + b.pcb_x * 0.08, mz + pcb_t), b.pcb_x * 0.84, comp, fc="#BFD8C9",
                               ec="#174D30", lw=0.6, ls="--"))
        ax.text(C / 2, mz + pcb_t + comp / 2, "envelope de componentes", ha="center", va="center",
                fontsize=8, color="#174D30")
    d = max(C, A) * 0.06
    _dim(ax, (C + d, 0), (C + d, A), f"A = {A:.0f}", (d * 1.1, 0), rotation=90)
    _dim(ax, (-d, mz), (-d, mz + b.assembly_height), f"Z = {fmt_mm(b.assembly_height, 0)}",
         (-d * 1.2, 0), rotation=90)
    ax.text(-d * 0.3, mz / 2, f"{fmt_mm(mz, 0)}", ha="right", va="center", fontsize=8.5, color=BRAND)
    ax.text(-d * 0.3, A - mz / 2, f"{fmt_mm(mz, 0)}", ha="right", va="center", fontsize=8.5, color=BRAND)
    _dim(ax, (0, -d), (C, -d), f"C = {C:.0f}", (0, -d * 0.7))
    ax.set_xlim(-d * 3.4, C + d * 3.0)
    ax.set_ylim(-d * 2.4, A + d * 1.0)
    ax.set_aspect("equal")
    ax.axis("off")


# --------------------------------------------------------------------------- #
# Planificação FEFCO 0201
# --------------------------------------------------------------------------- #
def _draw_blank_0201(ax, b):
    C, L, A = b.length, b.width, b.height
    g = GLUE_FLAP_0201
    fh = L / 2                      # altura das abas
    xs = [0, g, g + C, g + C + L, g + 2 * C + L, g + 2 * C + 2 * L]
    total_w, total_h = xs[-1], A + 2 * fh
    cut = dict(color=LINE, lw=1.5)
    crease = dict(color="#B5651D", lw=1.0, ls="--")
    # aba de cola (trapézio)
    tap = min(g * 0.5, A * 0.15)
    ax.plot([xs[1], 0, 0, xs[1]], [fh, fh + tap, fh + A - tap, fh + A], **cut)
    ax.add_patch(Polygon([(xs[1], fh), (0, fh + tap), (0, fh + A - tap), (xs[1], fh + A)],
                         fc=KRAFT_LEFT, ec="none", alpha=0.6))
    for i in range(1, 5):
        xa, xb = xs[i], xs[i + 1]
        ax.add_patch(Rectangle((xa, 0), xb - xa, total_h, fc=KRAFT_TOP, ec="none", alpha=0.7))
        # cortes das abas (ranhuras) e contorno
        ax.plot([xa, xb], [0, 0], **cut)
        ax.plot([xa, xb], [total_h, total_h], **cut)
    for x in xs[1:]:
        ax.plot([x, x], [0, fh], **cut)
        ax.plot([x, x], [fh + A, total_h], **cut)
    ax.plot([xs[-1], xs[-1]], [fh, fh + A], **cut)
    # vincos
    ax.plot([xs[1], xs[-1]], [fh, fh], **crease)
    ax.plot([xs[1], xs[-1]], [fh + A, fh + A], **crease)
    for x in xs[1:-1]:
        ax.plot([x, x], [fh, fh + A], **crease)
    # identificação dos painéis
    names = ["C", "L", "C", "L"]
    for i, n in enumerate(names):
        xm = (xs[i + 1] + xs[i + 2]) / 2
        ax.text(xm, fh + A / 2, n, ha="center", va="center", fontsize=13, fontweight="bold", color=LINE)
        ax.text(xm, fh / 2, "aba", ha="center", va="center", fontsize=8, color="#6B4E2A")
        ax.text(xm, fh + A + fh / 2, "aba", ha="center", va="center", fontsize=8, color="#6B4E2A")
    d = max(total_w, total_h) * 0.045
    for i in range(5):
        _dim(ax, (xs[i], total_h + d), (xs[i + 1], total_h + d), f"{xs[i + 1] - xs[i]:.0f}", (0, d * 0.55))
    _dim(ax, (0, -d * 1.3), (total_w, -d * 1.3), f"Chapa ≈ {total_w:.0f}", (0, -d * 0.65))
    _dim(ax, (total_w + d, 0), (total_w + d, fh), f"{fh:.0f}", (d * 0.9, 0), rotation=90)
    _dim(ax, (total_w + d, fh), (total_w + d, fh + A), f"{A:.0f}", (d * 0.9, 0), rotation=90)
    _dim(ax, (total_w + d, fh + A), (total_w + d, total_h), f"{fh:.0f}", (d * 0.9, 0), rotation=90)
    _dim(ax, (total_w + d * 3.2, 0), (total_w + d * 3.2, total_h), f"≈ {total_h:.0f}", (d * 0.9, 0), rotation=90)
    ax.plot([], [], **cut, label="Corte")
    ax.plot([], [], **crease, label="Vinco (dobra)")
    ax.legend(loc="lower right", ncol=2, frameon=False, fontsize=9)
    ax.set_xlim(-d, total_w + d * 5)
    ax.set_ylim(-d * 3.2, total_h + d * 2.2)
    ax.set_aspect("equal")
    ax.axis("off")


def _draw_0427_note(ax, b):
    ax.axis("off")
    ax.text(0.5, 0.62, "FEFCO 0427 — caixa corte e vinco tipo maleta", ha="center", fontsize=13,
            fontweight="bold", color=BRAND, transform=ax.transAxes)
    ax.text(0.5, 0.42, "A planificação e a faca de corte seguem o padrão FEFCO 0427 do fabricante,\n"
            f"a partir das medidas internas {b.text}.\nSolicitar ao fabricante o desenho da faca para aprovação.",
            ha="center", va="center", fontsize=10.5, color="#333", transform=ax.transAxes, linespacing=1.6)


# --------------------------------------------------------------------------- #
# Carimbo
# --------------------------------------------------------------------------- #
def spec_rows(result: AnalysisResult, spec: PackagingSpec) -> list[tuple[str, str]]:
    b = box_dimensions(result)
    rows = [
        ("Projeto / PCI", spec.project or "—"),
        ("Modelo da caixa", f"{spec.model} — {BOX_MODELS.get(spec.model, '')}"),
        ("Medidas INTERNAS (C x L x A)", b.text),
        ("Material", spec.material),
        ("Quantidade de caixas", str(spec.quantity) if spec.quantity else "A definir"),
        ("Conteúdo por caixa", f"1 PCI {fmt_mm(b.pcb_x)} x {fmt_mm(b.pcb_y)} mm, altura montada "
                               f"{fmt_mm(b.assembly_height, 0)} mm"),
        ("Proteção interna", f"Saco antiestático (ESD) + plástico bolha; folga {fmt_mm(b.margin_xy, 0)} mm "
                             f"por lado e {fmt_mm(b.margin_z, 0)} mm acima/abaixo"),
    ]
    if spec.model == "FEFCO 0201":
        w, h = b.blank_0201()
        rows.append(("Chapa planificada (ref.)", f"≈ {w:.0f} x {h:.0f} mm (aba de cola {GLUE_FLAP_0201:.0f} mm)"))
    rows.append(("Impressão", "Sem impressão (salvo indicação)"))
    if spec.notes:
        rows.append(("Observações", spec.notes))
    rows.append(("Emitente / data", f"VRI Indústria Eletrônica S.A. — {datetime.now():%d/%m/%Y}"))
    return rows


def _draw_spec(ax, result, spec):
    """Quadro de especificação desenhado à mão (quebra de linha controlada)."""
    import textwrap

    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    rows = spec_rows(result, spec)
    wrapped = [(textwrap.fill(a, 18), textwrap.fill(b, 34)) for a, b in rows]
    lines = [max(a.count("\n"), b.count("\n")) + 1 for a, b in wrapped]
    head = 1.6
    unit = 1.0 / (sum(lines) + head + 0.6 * len(rows))
    y = 1.0
    ax.add_patch(Rectangle((0, y - head * unit), 1, head * unit, fc=BRAND, ec=BRAND))
    ax.text(0.5, y - head * unit / 2, "ESPECIFICAÇÃO PARA COTAÇÃO", ha="center", va="center", color="white",
            fontsize=10.5, fontweight="bold")
    y -= head * unit
    split = 0.36
    for (a, b), n, (raw_a, _) in zip(wrapped, lines, rows):
        h = (n + 0.6) * unit
        ax.add_patch(Rectangle((0, y - h), split, h, fc="#F1F3FA", ec="#C5C9D6", lw=0.8))
        ax.add_patch(Rectangle((split, y - h), 1 - split, h, fc="white", ec="#C5C9D6", lw=0.8))
        hl = raw_a.startswith("Medidas")
        ax.text(0.02, y - h / 2, a, ha="left", va="center", fontsize=8.6, fontweight="bold",
                color=BRAND if hl else "#222", linespacing=1.25)
        ax.text(split + 0.02, y - h / 2, b, ha="left", va="center", fontsize=9.6 if hl else 8.6,
                fontweight="bold" if hl else "normal", color=BRAND if hl else "#222", linespacing=1.25)
        y -= h


# --------------------------------------------------------------------------- #
def render_packaging(result: AnalysisResult, spec: PackagingSpec | None = None, dpi: int = 130) -> bytes:
    spec = spec or PackagingSpec()
    b = box_dimensions(result)
    fig = Figure(figsize=(16.5, 11.0), facecolor="white")
    FigureCanvasAgg(fig)
    fig.text(0.5, 0.972, "Desenho de Embalagem — Caixa para PCI", ha="center", fontsize=17,
             fontweight="bold", color=BRAND)
    fig.text(0.5, 0.945, f"{spec.model}  |  Medidas internas {b.text}  |  {spec.material}", ha="center",
             fontsize=11, color="#333")
    ax_iso = fig.add_axes([0.02, 0.47, 0.40, 0.45])
    _panel_title(ax_iso, "Croqui da caixa (isométrico)")
    _draw_iso(ax_iso, b.length, b.width, b.height, spec.model)
    ax_top = fig.add_axes([0.45, 0.66, 0.26, 0.26])
    _panel_title(ax_top, "Vista superior (interna)")
    _draw_top(ax_top, b)
    ax_sec = fig.add_axes([0.45, 0.45, 0.26, 0.19])
    _panel_title(ax_sec, "Corte lateral")
    _draw_section(ax_sec, b)
    ax_spec = fig.add_axes([0.735, 0.47, 0.25, 0.445])
    _draw_spec(ax_spec, result, spec)
    ax_blank = fig.add_axes([0.04, 0.05, 0.92, 0.36])
    if spec.model == "FEFCO 0201":
        _panel_title(ax_blank, "Planificação de referência — FEFCO 0201 (medidas internas, sem acréscimos de vinco)")
        _draw_blank_0201(ax_blank, b)
    else:
        _draw_0427_note(ax_blank, b)
    fig.text(0.5, 0.012, "Cotas em milímetros. Medidas internas: o fabricante deve aplicar os acréscimos de vinco "
             "conforme a espessura da onda. Desenho fora de escala entre vistas.", ha="center", fontsize=8.5,
             color="#555")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, facecolor="white")
    return buf.getvalue()
