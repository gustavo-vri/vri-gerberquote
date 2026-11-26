"""Exportação do PDF Técnico Consolidado (ReportLab).

Conteúdo:
* Cabeçalho institucional (logo VRI + título) em todas as páginas;
* Tabela de Resumo Comercial;
* Avisos de montagem em destaque;
* Somente os desenhos aplicáveis: Dimensional e Furação (desenho único), Montagem
  TOP e/ou BOT (lados sem montagem são omitidos e substituídos pelo aviso) e Embalagem;
* Lista de arquivos analisados.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether, NextPageTemplate, PageBreak,
                                PageTemplate, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

from core.analyzer import AnalysisResult, BoardData, fmt_mm
from core.classifier import ClassifiedFile, LayerType
from core.packaging import PackagingSpec
from core.paths import resource_path
from core.timing import TimeParams, fmt_time, time_rows
from render.gerber_renderer import render_all
from render.package_renderer import render_packaging

BRAND = colors.HexColor("#000099")
TITLE = "VRI Indústria Eletrônica S.A. - Análise Técnica para Orçamento"
DEFAULT_LOGO = resource_path("assets/logo_vri.png")


@dataclass
class PdfReport:
    path: Path
    sections: list[str] = field(default_factory=list)   # desenhos incluídos
    notices: list[str] = field(default_factory=list)    # avisos exibidos
    pages: int = 0


def _styles():
    ss = getSampleStyleSheet()
    return {
        "h1": ParagraphStyle("h1", parent=ss["Heading1"], textColor=BRAND, fontSize=14, spaceAfter=6,
                             spaceBefore=4),
        "body": ParagraphStyle("body", parent=ss["BodyText"], fontSize=9.5, leading=12),
        "small": ParagraphStyle("small", parent=ss["BodyText"], fontSize=8, leading=10,
                                textColor=colors.HexColor("#444444")),
        "cell": ParagraphStyle("cell", parent=ss["BodyText"], fontSize=9, leading=11),
        "alert": ParagraphStyle("alert", parent=ss["BodyText"], fontSize=12, leading=15,
                                alignment=TA_CENTER, textColor=colors.HexColor("#B00020"),
                                fontName="Helvetica-Bold"),
        "warn": ParagraphStyle("warn", parent=ss["BodyText"], fontSize=11, leading=14,
                               alignment=TA_CENTER, textColor=colors.HexColor("#8A5A00"),
                               fontName="Helvetica-Bold"),
    }


def _notice_box(text: str, style: ParagraphStyle, bg: str, border: str) -> Table:
    t = Table([[Paragraph(text, style)]], colWidths=[180 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(bg)),
        ("BOX", (0, 0), (-1, -1), 1.2, colors.HexColor(border)),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def _image(png: bytes, max_w: float = 180 * mm, max_h: float = 205 * mm) -> Image:
    reader = ImageReader(io.BytesIO(png))
    iw, ih = reader.getSize()
    scale = min(max_w / iw, max_h / ih)
    return Image(io.BytesIO(png), width=iw * scale, height=ih * scale)


def _grid_table(rows, col_widths, header=True, zebra=True) -> Table:
    t = Table(rows, colWidths=col_widths, repeatRows=1 if header else 0)
    style = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C5C9D6")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), BRAND),
                  ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                  ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9)]
    if zebra:
        for r in range(1 if header else 0, len(rows)):
            if r % 2 == 0:
                style.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#F1F3FA")))
    t.setStyle(TableStyle(style))
    return t


def summary_rows(result: AnalysisResult, project_name: str = "") -> list[tuple[str, str]]:
    """Linhas do Resumo Comercial (também usadas pela interface)."""
    rows = []
    if project_name:
        rows.append(("Projeto", project_name))
    rows += [
        ("Número de Layers", f"{result.layers}" + ("" if result.layers == result.layers_auto
                                                   else f" (detectado: {result.layers_auto})")),
        ("Formato da PCI", result.shape),
        ("Dimensões da PCI (X x Y)", result.board_text + ("" if result.shape in ("Retangular", "Quadrado")
                                                          else "  (envelope máximo)")),
        ("Área (envelope)", f"{fmt_mm(result.area_cm2)} cm²"
         + (f"  |  área real {fmt_mm(result.real_area_cm2)} cm²"
            if abs(result.real_area_cm2 - result.area_cm2) > 0.05 else "")),
        ("Componentes SMD Top", f"{result.comp_smd_top}"),
        ("Componentes SMD Bot", f"{result.comp_smd_bot}"),
        ("Componentes PTH", f"{result.comp_pth}"),
        ("Fonte da contagem de componentes", (result.components.source if result.components else "-")),
        ("Pads SMD Top", f"{result.smd_top}  [{result.smd_top_source}]"),
        ("Pads SMD Bot", f"{result.smd_bot}  [{result.smd_bot_source}]"),
        ("Furos PTH (total)", f"{result.pth_total}  (componentes: {result.pth_component} | vias: {result.via_count})"),
        ("Furos NPTH", f"{result.npth_count}"),
        ("Status Montagem TOP", result.status_top),
        ("Status Montagem BOT", result.status_bot),
        ("Altura Est. Montagem (Z)", f"{fmt_mm(result.assembly_height, 1)} mm"
         + ("" if result.assembly_height == result.assembly_height_auto
            else f" (manual; regra: {fmt_mm(result.assembly_height_auto, 1)} mm)")),
        ("Embalagem Sugerida (X x Y x Z)", result.package_text),
    ]
    if result.times:
        t = result.times
        rows += [
            ("Tempo SMD por placa", fmt_time(t.smd_unit_s)),
            ("Tempo PTH por placa", fmt_time(t.pth_unit_s)),
            (f"Tempo total do lote ({t.lot_qty} placas)",
             f"{t.lot_total_h:.2f} h (setup {t.setup_total_min:.0f} min)".replace(".", ",", 1)),
        ]
    return rows


def _make_on_page(logo: Path, project_name: str, stamp: str, pagesize, title: str = TITLE):
    def on_page(canvas, doc):
        w, h = pagesize
        canvas.saveState()
        canvas.setFillColor(BRAND)
        canvas.rect(0, h - 26 * mm, w, 26 * mm, fill=1, stroke=0)
        x_text = 15 * mm
        if logo.exists():
            try:
                ir = ImageReader(str(logo))
                iw, ih = ir.getSize()
                lh = 18 * mm
                lw = iw * lh / ih
                canvas.drawImage(ir, 12 * mm, h - 22 * mm, width=lw, height=lh, mask="auto")
                x_text = 12 * mm + lw + 6 * mm
            except Exception:  # logo inválido não deve impedir o relatório
                pass
        else:
            canvas.setFillColor(colors.white)
            canvas.setFont("Helvetica-Bold", 22)
            canvas.drawString(15 * mm, h - 16 * mm, "VRI")
            x_text = 40 * mm
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 11.5)
        canvas.drawString(x_text, h - 12.5 * mm, title)
        canvas.setFont("Helvetica", 8.5)
        canvas.drawString(x_text, h - 18.5 * mm, f"Projeto: {project_name or '-'}   |   Emitido em {stamp}")
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(15 * mm, 9 * mm, "Gerado por VRI GerberQuote - documento de apoio ao orçamento; "
                                           "valores estimados a partir dos arquivos Gerber/Drill do cliente.")
        canvas.drawRightString(w - 15 * mm, 9 * mm, f"Página {doc.page}")
        canvas.setStrokeColor(BRAND)
        canvas.setLineWidth(0.8)
        canvas.line(15 * mm, 13 * mm, w - 15 * mm, 13 * mm)
        canvas.restoreState()
    return on_page


def generate_pdf(output: str | Path, board: BoardData, result: AnalysisResult,
                 classified: list[ClassifiedFile] | None = None, project_name: str = "",
                 logo_path: str | Path | None = None, dpi: int = 170,
                 pth_sides: tuple[bool, bool] | None = None,
                 packaging: PackagingSpec | None = None,
                 time_params: TimeParams | None = None) -> PdfReport:
    """PDF Técnico Consolidado (A4 retrato)."""
    output = Path(output)
    logo = Path(logo_path) if logo_path else DEFAULT_LOGO
    st = _styles()
    report = PdfReport(path=output)
    packaging = packaging or PackagingSpec(project=project_name)
    images = render_all(board, result, dpi=dpi, packaging=packaging)
    stamp = datetime.now().strftime("%d/%m/%Y %H:%M")
    on_page = _make_on_page(logo, project_name, stamp, A4)
    on_page_land = _make_on_page(logo, project_name, stamp, landscape(A4))

    story: list = []
    story.append(Paragraph("Resumo Comercial", st["h1"]))
    rows = [["Item", "Valor"]] + [[a, Paragraph(b, st["cell"])] for a, b in summary_rows(result, project_name)]
    story.append(_grid_table(rows, [62 * mm, 118 * mm]))
    if pth_sides is not None:
        story.append(Spacer(1, 2 * mm))
        sides = " e ".join(n for n, on in zip(("TOP", "BOT"), pth_sides) if on) or "nenhum"
        story.append(Paragraph(f"Lado de inserção PTH confirmado pelo orçamentista: {sides}.", st["small"]))

    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph("Status de Montagem", st["h1"]))
    for status in (result.top, result.bot):
        if not status.has_assembly:
            story.append(_notice_box(status.message, st["alert"], "#FDE2E1", "#B00020"))
            report.notices.append(status.message)
        elif status.is_partial:
            story.append(_notice_box(status.message, st["warn"], "#FFF3CD", "#8A5A00"))
            report.notices.append(status.message)
        else:
            story.append(_notice_box(status.message, st["body"], "#E7F5EC", "#0B8F3A"))
        story.append(Spacer(1, 2.5 * mm))
    if result.warnings:
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph("<b>Observações da análise:</b>", st["body"]))
        for w in result.warnings:
            story.append(Paragraph(f"• {w}", st["small"]))

    def drawing(title: str, key: str):
        story.append(PageBreak())
        story.append(KeepTogether([Paragraph(title, st["h1"]), _image(images[key], max_h=215 * mm)]))
        report.sections.append(title)

    drawing("Dimensional e Furação", "dimdrill")
    for status, key in ((result.top, "top"), (result.bot, "bot")):
        if images[key] is not None:
            drawing(f"Montagem Lado {status.side}", key)
        else:
            story.append(Spacer(1, 4 * mm))
            story.append(_notice_box(f"{status.message} — desenho de montagem {status.side} omitido.",
                                     st["alert"], "#FDE2E1", "#B00020"))
    # Componentes e tempos de montagem
    if result.times and result.components:
        story.append(PageBreak())
        story.append(Paragraph("Componentes e Tempos de Montagem", st["h1"]))
        comps = result.components
        story.append(Paragraph(f"<b>Fonte da contagem:</b> {comps.source}", st["body"]))
        if not comps.exact:
            story.append(Paragraph("Contagem estimada pela geometria dos pads/furos e serigrafia; recomenda-se "
                                   "solicitar o arquivo Pick &amp; Place (centroide) ao cliente.", st["small"]))
        story.append(Spacer(1, 3 * mm))
        trows = [["Etapa", "Base de cálculo", "Tempo"]] + [
            [Paragraph(a, st["cell"]), Paragraph(b, st["cell"]), Paragraph(c, st["cell"])]
            for a, b, c in time_rows(result.times, time_params or TimeParams())]
        tt = _grid_table(trows, [70 * mm, 80 * mm, 30 * mm])
        story.append(tt)
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph("SMD: impressão de pasta + pick &amp; place + forno de refusão (tempo por placa = "
                               "takt da linha + inspeção). PTH: montagem manual conforme IPC-A-610 e "
                               "IPC J-STD-001. Parâmetros de referência configuráveis no sistema.", st["small"]))
        report.sections.append("Componentes e Tempos")

    # Embalagem em página paisagem (desenho largo, legível para o fabricante de caixas)
    story.append(NextPageTemplate("paisagem"))
    story.append(PageBreak())
    story.append(Paragraph("Embalagem Sugerida", st["h1"]))
    story.append(_image(images["package"], max_w=262 * mm, max_h=138 * mm))
    report.sections.append("Embalagem Sugerida")
    story.append(NextPageTemplate("retrato"))

    if classified:
        story.append(PageBreak())
        story.append(Paragraph("Arquivos Analisados", st["h1"]))
        frows = [["Arquivo", "Tipo de camada", "Critério"]]
        for cf in classified:
            if cf.layer_type == LayerType.IGNORE:
                continue
            frows.append([Paragraph(cf.source.relpath, st["cell"]), cf.layer_type.value,
                          Paragraph(cf.reason, st["cell"])])
        story.append(_grid_table(frows, [75 * mm, 55 * mm, 50 * mm]))

    doc = BaseDocTemplate(str(output), pagesize=A4, title=TITLE, author="VRI Indústria Eletrônica S.A.",
                          subject=project_name)
    pw, ph = A4
    lw, lh = landscape(A4)
    doc.addPageTemplates([
        PageTemplate("retrato", [Frame(15 * mm, 18 * mm, pw - 30 * mm, ph - 50 * mm, id="r")],
                     onPage=on_page, pagesize=A4),
        PageTemplate("paisagem", [Frame(15 * mm, 16 * mm, lw - 30 * mm, lh - 46 * mm, id="p")],
                     onPage=on_page_land, pagesize=landscape(A4)),
    ])
    doc.build(story)
    report.pages = doc.page
    return report


def generate_package_pdf(output: str | Path, result: AnalysisResult, spec: PackagingSpec,
                         logo_path: str | Path | None = None, dpi: int = 200) -> PdfReport:
    """Desenho de embalagem avulso (A4 paisagem), para envio ao fabricante de caixas.

    Não contém dados comerciais da placa além do necessário para a caixa.
    """
    output = Path(output)
    logo = Path(logo_path) if logo_path else DEFAULT_LOGO
    page = landscape(A4)
    stamp = datetime.now().strftime("%d/%m/%Y %H:%M")
    on_page = _make_on_page(logo, spec.project, stamp, page,
                            "VRI Indústria Eletrônica S.A. - Solicitação de Cotação de Embalagem")
    png = render_packaging(result, spec, dpi=dpi)
    story = [_image(png, max_w=267 * mm, max_h=158 * mm)]
    doc = SimpleDocTemplate(str(output), pagesize=page, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=30 * mm, bottomMargin=16 * mm,
                            title="Desenho de Embalagem", author="VRI Indústria Eletrônica S.A.",
                            subject=spec.project)
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return PdfReport(path=output, sections=["Embalagem"], pages=doc.page)
