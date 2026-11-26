"""Validação automática do VRI GerberQuote com projetos sintéticos padrão Altium.

Cenários obrigatórios:
1. 4 layers, pastas /Gerber e /Drill separadas, SMD Top + PTH, lado Bot vazio;
2. 2 layers, apenas SMD Top e Bot, sem PTH (somente NPTH mecânicos);
3. 1 layer, apenas PTH (sem SMD).
Além disso: regras de embalagem, classificação de outros CADs, parsers e PDF.
"""
from __future__ import annotations

import os
import zipfile

import pytest

from core.analyzer import (AnalysisOptions, SideStatus, analyze_project, estimate_height,
                           package_dimensions, side_message)
from core.classifier import LayerType, classify_drill, classify_file
from core.extractor import SourceFile, extract_project
from core.parsers import parse_excellon, parse_gerber
from export.pdf_generator import generate_pdf
from render.gerber_renderer import render_all
from core.geometry import analyze_outline
from core.packaging import PackagingSpec, box_dimensions
from export.pdf_generator import generate_package_pdf
from render.package_renderer import render_packaging, spec_rows
from tests.synthetic import (GerberWriter, excellon_altium, scenario_1l_pth_only, scenario_2l_smd_both,
                             scenario_4l_smdtop_pth, scenario_l_shape)


@pytest.fixture
def session_factory(tmp_path):
    sessions = []

    def make(builder, options=None, name=None):
        path = builder(tmp_path / (name or f"{builder.__name__}.zip"))
        s = analyze_project(path, options)
        sessions.append(s)
        return s

    yield make
    for s in sessions:
        s.project.cleanup()


def _types(session):
    return {c.source.relpath: c.layer_type for c in session.classified}


# =========================================================================== #
# Cenário 1 — 4 layers, /Gerber + /Drill, SMD Top + PTH, Bot vazio
# =========================================================================== #
class TestCenario1QuatroLayersSmdTopPth:
    def test_varredura_recursiva_e_classificacao(self, session_factory):
        s = session_factory(scenario_4l_smdtop_pth)
        t = _types(s)
        assert t["Gerber/Proj.GTL"] == LayerType.TOP_COPPER
        assert t["Gerber/Proj.GBL"] == LayerType.BOTTOM_COPPER
        assert t["Gerber/Proj.G1"] == LayerType.INNER
        assert t["Gerber/Proj.G2"] == LayerType.INNER
        assert t["Gerber/Proj.GTP"] == LayerType.TOP_PASTE
        assert t["Gerber/Proj.GBP"] == LayerType.BOTTOM_PASTE
        assert t["Gerber/Proj.GTO"] == LayerType.TOP_SILK
        assert t["Gerber/Proj.GKO"] == LayerType.OUTLINE
        assert t["Drill/Proj-Plated.TXT"] == LayerType.DRILL_PTH
        assert t["Drill/Proj-NonPlated.TXT"] == LayerType.DRILL_NPTH

    def test_arquivos_irrelevantes_ignorados(self, session_factory):
        s = session_factory(scenario_4l_smdtop_pth)
        ignored = set(s.project.ignored)
        for f in ("Gerber/Fab Notes.pdf", "Gerber/Proj.IPC", "Gerber/Status Report.Txt",
                  "Gerber/Proj.BOM.txt", "Drill/Proj-RoundHoles.TXT", "Gerber/Proj.DRR"):
            assert f in ignored
        assert len(s.classified) == 10

    def test_resumo_comercial(self, session_factory):
        r = session_factory(scenario_4l_smdtop_pth).result
        assert r.layers == 4
        assert r.board_x == pytest.approx(100.0, abs=0.01)
        assert r.board_y == pytest.approx(80.0, abs=0.01)
        assert r.area_cm2 == pytest.approx(80.0, abs=0.05)
        assert r.smd_top == 25          # 24 flashes + 1 região (pad térmico)
        assert r.smd_bot == 0           # GBP presente porém vazio
        assert r.pth_total == 13 and r.pth_component == 10 and r.via_count == 3
        assert r.npth_count == 4

    def test_lado_bot_sem_montagem_e_desenho_omitido(self, session_factory):
        s = session_factory(scenario_4l_smdtop_pth)
        r = s.result
        assert r.status_bot == "LADO BOT: SEM MONTAGEM"
        assert not r.bot.has_view
        assert r.status_top == "LADO TOP: MONTAGEM SMD + PTH"
        imgs = render_all(s.board, r, dpi=60)
        assert imgs["bot"] is None
        assert imgs["top"].startswith(b"\x89PNG")

    def test_embalagem(self, session_factory):
        r = session_factory(scenario_4l_smdtop_pth).result
        assert r.assembly_height == 25.0          # PTH em 1 lado, lado oposto vazio
        assert r.package == pytest.approx((120.0, 100.0, 35.0), abs=0.01)
        assert r.package_text == "120 x 100 x 35 mm"

    def test_pdf_omite_bot_e_exibe_aviso(self, session_factory, tmp_path):
        s = session_factory(scenario_4l_smdtop_pth)
        rep = generate_pdf(tmp_path / "c1.pdf", s.board, s.result, s.classified, "Proj", dpi=60)
        assert rep.path.exists() and rep.path.stat().st_size > 10_000
        assert "Montagem Lado TOP" in rep.sections
        assert "Montagem Lado BOT" not in rep.sections
        assert {"Dimensional e Furação", "Embalagem Sugerida"} <= set(rep.sections)
        assert "LADO BOT: SEM MONTAGEM" in rep.notices


# =========================================================================== #
# Cenário 2 — 2 layers, SMD Top e Bot, sem PTH (apenas NPTH)
# =========================================================================== #
class TestCenario2DuasLayersSmdDoisLados:
    def test_classificacao_drill_pelo_cabecalho(self, session_factory):
        s = session_factory(scenario_2l_smd_both)
        t = _types(s)
        assert t["SMD_Board.TXT"] == LayerType.DRILL_NPTH          # ;TYPE=NON_PLATED
        assert t["SMD_Board.GM1"] == LayerType.OUTLINE
        assert t["SMD_Board.GBO"] == LayerType.BOTTOM_SILK

    def test_resumo_e_status(self, session_factory):
        r = session_factory(scenario_2l_smd_both).result
        assert r.layers == 2
        assert r.board_x == pytest.approx(60.0, abs=0.01)   # contorno com cantos em arco (G03)
        assert r.board_y == pytest.approx(40.0, abs=0.01)
        assert r.smd_top == 15 and r.smd_bot == 8
        assert r.pth_total == 0 and r.pth_component == 0
        assert r.npth_count == 4
        assert r.status_top == "LADO TOP: SEM MONTAGEM PTH (APENAS SMD)"
        assert r.status_bot == "LADO BOT: SEM MONTAGEM PTH (APENAS SMD)"
        assert r.top.has_view and r.bot.has_view

    def test_embalagem_smd_dois_lados(self, session_factory):
        r = session_factory(scenario_2l_smd_both).result
        assert r.assembly_height == 20.0
        assert r.package == pytest.approx((80.0, 60.0, 30.0), abs=0.01)

    def test_pdf_com_os_dois_lados(self, session_factory, tmp_path):
        s = session_factory(scenario_2l_smd_both)
        rep = generate_pdf(tmp_path / "c2.pdf", s.board, s.result, s.classified, "SMD", dpi=60)
        assert {"Montagem Lado TOP", "Montagem Lado BOT"} <= set(rep.sections)
        assert "LADO TOP: SEM MONTAGEM PTH (APENAS SMD)" in rep.notices
        assert "LADO BOT: SEM MONTAGEM PTH (APENAS SMD)" in rep.notices


# =========================================================================== #
# Cenário 3 — 1 layer, apenas PTH
# =========================================================================== #
class TestCenario3UmaLayerApenasPth:
    def test_resumo_e_status(self, session_factory):
        s = session_factory(scenario_1l_pth_only)
        r = s.result
        assert r.layers == 1
        assert (r.smd_top, r.smd_bot) == (0, 0)
        assert r.pth_component == 8 and r.npth_count == 0
        assert r.status_top == "LADO TOP: SEM MONTAGEM SMD (APENAS PTH)"
        assert r.status_bot == "LADO BOT: SEM MONTAGEM"
        assert r.assembly_height == 25.0
        assert r.package == pytest.approx((70.0, 50.0, 35.0), abs=0.01)
        imgs = render_all(s.board, r, dpi=60)
        assert imgs["top"] is not None and imgs["bot"] is None

    def test_checkbox_pth_no_bot(self, session_factory):
        s = session_factory(scenario_1l_pth_only)
        r = s.reevaluate(AnalysisOptions(pth_top=True, pth_bot=True))
        assert r.status_bot == "LADO BOT: SEM MONTAGEM SMD (APENAS PTH)"
        assert r.assembly_height == 30.0          # PTH + componentes no lado oposto
        r = s.reevaluate(AnalysisOptions(pth_top=False, pth_bot=True))
        assert r.status_top == "LADO TOP: SEM MONTAGEM"
        assert r.assembly_height == 25.0

    def test_nenhum_lado_pth_marcado_gera_alerta(self, session_factory):
        s = session_factory(scenario_1l_pth_only)
        r = s.reevaluate(AnalysisOptions(pth_top=False, pth_bot=False))
        assert r.status_top == "LADO TOP: SEM MONTAGEM" and r.assembly_height == 10.0
        assert any("nenhum lado" in w for w in r.warnings)


# =========================================================================== #
# Regras de negócio e overrides
# =========================================================================== #
@pytest.mark.parametrize("top,bot,expected", [
    ((0, 0), (0, 0), 10.0),
    ((5, 0), (0, 0), 15.0),
    ((0, 0), (5, 0), 15.0),
    ((5, 0), (5, 0), 20.0),
    ((5, 3), (0, 0), 25.0),
    ((0, 3), (0, 0), 25.0),
    ((0, 3), (5, 0), 30.0),
    ((5, 3), (0, 3), 30.0),
])
def test_regra_altura(top, bot, expected):
    t = SideStatus("TOP", top[0], top[1], side_message("TOP", *top))
    b = SideStatus("BOT", bot[0], bot[1], side_message("BOT", *bot))
    assert estimate_height(t, b) == expected


def test_margens_embalagem():
    assert package_dimensions(100, 50, 15) == (120, 70, 25)


def test_overrides_manuais(session_factory):
    s = session_factory(scenario_2l_smd_both)
    r = s.reevaluate(AnalysisOptions(layers_override=6, height_override=42.0))
    assert r.layers == 6 and r.layers_auto == 2
    assert r.assembly_height == 42.0 and r.assembly_height_auto == 20.0
    assert r.package[2] == 52.0


def test_limiar_de_via(session_factory):
    s = session_factory(scenario_4l_smdtop_pth)
    r = s.reevaluate(AnalysisOptions(via_max_diameter=0.0))
    assert r.pth_component == 13 and r.via_count == 0


def test_placa_smd_com_vias_nao_conta_como_pth(tmp_path):
    top, gtp, gko = GerberWriter("Top"), GerberWriter("Paste"), GerberWriter("KO")
    gko.rect_outline(30, 20)
    gtp.flash_rect(10, 10, 1, 0.6)
    top.flash_rect(10, 10, 1, 0.6)
    z = tmp_path / "vias.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("v.GTL", top.render()); f.writestr("v.GTP", gtp.render())
        f.writestr("v.GKO", gko.render())
        f.writestr("v.DRL", excellon_altium([(5, 5, 0.3), (6, 5, 0.25)], True))
    s = analyze_project(z)
    assert s.result.pth_total == 2 and s.result.pth_component == 0
    assert s.result.status_top == "LADO TOP: SEM MONTAGEM PTH (APENAS SMD)"
    assert s.result.assembly_height == 15.0
    s.project.cleanup()


def test_paste_ausente_nao_conta_smd_e_sugere_pela_mascara(tmp_path):
    top, gts, gko = GerberWriter("Top"), GerberWriter("Mask"), GerberWriter("KO")
    gko.rect_outline(30, 20)
    for x in (5, 8, 11):
        gts.flash_rect(x, 10, 1.1, 0.7)       # 3 aberturas de pads SMD
    gts.flash_circle(20, 10, 2.0)             # abertura de um pad PTH (coincide com furo)
    z = tmp_path / "mask.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("m.GTL", top.render()); f.writestr("m.GTS", gts.render())
        f.writestr("m.GKO", gko.render())
        f.writestr("m-Plated.TXT", excellon_altium([(20, 10, 1.0)], True))
    s = analyze_project(z)
    assert s.result.smd_top == 0                      # SMD só pela Paste (regra)
    assert any("Paste TOP ausente" in w and "~3" in w for w in s.result.warnings)
    # orçamentista reclassifica a máscara como Paste no dropdown
    next(c for c in s.classified if c.name == "m.GTS").layer_type = LayerType.TOP_PASTE
    r = s.reload()
    assert r.smd_top == 4 and r.top.has_smd
    s.project.cleanup()


def test_drill_altium_misto_por_secao(tmp_path):
    text = """M48
;Layer_Color=9474304
;FILE_FORMAT=2:4
INCH,LZ
;TYPE=PLATED
T1F00S00C0.0280
T2F00S00C0.0400
;TYPE=NON_PLATED
T3F00S00C0.1260
%
T01
X01Y01
T02
X02Y01
T03
X03Y01
M30
"""
    df = parse_excellon(text)
    assert [h.plated for h in df.holes] == [True, True, False]
    assert df.holes[1].x == pytest.approx(50.8)       # "02" em LZ 2:4 = 2,0000 in
    assert df.file_plating is None
    sf = _sf(tmp_path, "proj.txt", text, "excellon")
    assert classify_file(sf).layer_type == LayerType.DRILL_MIXED


def test_sem_outline_usa_envelope_e_alerta(tmp_path):
    top = GerberWriter("Top")
    top.line(0, 0, 40, 0); top.line(0, 0, 0, 25)
    z = tmp_path / "noout.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("n.GTL", top.render())
    s = analyze_project(z)
    assert s.result.board_x > 39 and s.result.board_y > 24
    assert any("Contorno" in w for w in s.result.warnings)
    s.project.cleanup()


# =========================================================================== #
# Classificação de outros CADs
# =========================================================================== #
def _sf(tmp_path, name, content, fmt="gerber"):
    p = tmp_path / name
    p.write_text(content)
    return SourceFile(p, name, fmt)


@pytest.mark.parametrize("name,expected", [
    ("board-F_Cu.gbr", LayerType.TOP_COPPER),
    ("board-B_Cu.gbr", LayerType.BOTTOM_COPPER),
    ("board-In1_Cu.gbr", LayerType.INNER),
    ("board-In2_Cu.gbr", LayerType.INNER),
    ("board-F_Paste.gbr", LayerType.TOP_PASTE),
    ("board-B_Paste.gbr", LayerType.BOTTOM_PASTE),
    ("board-F_Silkscreen.gbr", LayerType.TOP_SILK),
    ("board-B_SilkS.gbr", LayerType.BOTTOM_SILK),
    ("board-F_Mask.gbr", LayerType.TOP_MASK),
    ("board-Edge_Cuts.gbr", LayerType.OUTLINE),
    ("top_copper.art", LayerType.TOP_COPPER),
    ("bottom_copper.art", LayerType.BOTTOM_COPPER),
    ("inner1.art", LayerType.INNER),
    ("top_silk.gbr", LayerType.TOP_SILK),
    ("bot_silk.gbr", LayerType.BOTTOM_SILK),
    ("top_paste.gbr", LayerType.TOP_PASTE),
    ("bot_paste.gbr", LayerType.BOTTOM_PASTE),
    ("BoardOutline.gbr", LayerType.OUTLINE),
    ("proj.GP1", LayerType.INNER),
    ("proj.G3", LayerType.INNER),
    ("proj.GML", LayerType.OUTLINE),
    ("proj.GM2", LayerType.MECHANICAL),
])
def test_classificacao_por_nome(tmp_path, name, expected):
    sf = _sf(tmp_path, name, "%FSLAX46Y46*%\n%MOMM*%\nM02*\n")
    assert classify_file(sf).layer_type == expected


def test_classificacao_gerber_x2(tmp_path):
    sf = _sf(tmp_path, "layer7.gbr", "%TF.FileFunction,Copper,L1,Top*%\n%FSLAX46Y46*%\n%MOMM*%\nM02*\n")
    assert classify_file(sf).layer_type == LayerType.TOP_COPPER
    sf = _sf(tmp_path, "layer8.gbr", "%TF.FileFunction,Paste,Bot*%\n%FSLAX46Y46*%\n%MOMM*%\nM02*\n")
    assert classify_file(sf).layer_type == LayerType.BOTTOM_PASTE


@pytest.mark.parametrize("name,head,expected", [
    ("Proj-Plated.TXT", "", LayerType.DRILL_PTH),
    ("Proj-NonPlated.TXT", "", LayerType.DRILL_NPTH),
    ("board-NPTH.drl", "", LayerType.DRILL_NPTH),
    ("board-PTH.drl", "", LayerType.DRILL_PTH),
    ("Proj.TXT", ";TYPE=NON_PLATED", LayerType.DRILL_NPTH),
    ("Proj.TXT", ";TYPE=PLATED", LayerType.DRILL_PTH),
    ("drill.xln", "M48\nMETRIC", LayerType.DRILL_MIXED),
])
def test_classificacao_drill(name, head, expected):
    assert classify_drill(name, head)[0] == expected


def test_extracao_de_pasta(tmp_path):
    (tmp_path / "Gerber").mkdir()
    (tmp_path / "Drill").mkdir()
    g = GerberWriter("Top"); g.flash_rect(1, 1, 1, 1)
    (tmp_path / "Gerber" / "p.GTL").write_text(g.render())
    (tmp_path / "Drill" / "p.DRL").write_text(excellon_altium([(1, 1, 1.0)], True))
    (tmp_path / "Drill" / "report.txt").write_text("relatório qualquer\n")
    proj = extract_project(tmp_path)
    assert sorted(f.relpath for f in proj.files) == ["Drill/p.DRL", "Gerber/p.GTL"]
    assert "Drill/report.txt" in proj.ignored


def test_zip_aninhado_e_zip_slip(tmp_path):
    inner = tmp_path / "inner.zip"
    g = GerberWriter("Top"); g.flash_rect(1, 1, 1, 1)
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("x.GTL", g.render())
    outer = tmp_path / "outer.zip"
    with zipfile.ZipFile(outer, "w") as z:
        z.write(inner, "sub/inner.zip")
        z.writestr("../../evil.GTL", g.render())
    proj = extract_project(outer)
    try:
        assert [f.name for f in proj.files] == ["x.GTL"]
        assert not (tmp_path.parent / "evil.GTL").exists()
    finally:
        proj.cleanup()


# =========================================================================== #
# Parsers
# =========================================================================== #
def test_parser_gerber_macro_e_obround():
    text = """%FSLAX46Y46*%
%MOMM*%
%AMRoundRect*
0 Rectangle with rounded corners*
4,1,4,$2,$3,$4,$5,$6,$7,$8,$9,$2,$3,0*
1,1,$1+$1,$2,$3*
1,1,$1+$1,$4,$5*
1,1,$1+$1,$6,$7*
1,1,$1+$1,$8,$9*
20,1,$1+$1,$2,$3,$4,$5,0*
20,1,$1+$1,$4,$5,$6,$7,0*
20,1,$1+$1,$6,$7,$8,$9,0*
20,1,$1+$1,$8,$9,$2,$3,0*%
%ADD10RoundRect,0.25X-0.5X-0.3X0.5X-0.3X0.5X0.3X-0.5X0.3*%
%ADD11O,1.0X2.0*%
D10*
X1000000Y2000000D03*
D11*
X3000000Y2000000D03*
M02*
"""
    layer = parse_gerber(text)
    assert layer.pad_count == 2
    assert layer.flashes[0].x == pytest.approx(1.0) and layer.flashes[0].y == pytest.approx(2.0)
    assert layer.flashes[0].aperture.extent() == pytest.approx(0.75, abs=0.02)
    x0, y0, x1, y1 = layer.bbox()
    assert x1 == pytest.approx(3.5) and y1 == pytest.approx(3.0)


def test_parser_excellon_formatos():
    kicad = """M48
; DRILL file {KiCad 8} date 2026
; FORMAT={-:-/ absolute / metric / decimal}
; #@! TF.FileFunction,MixedPlating,1,2
FMAT,2
METRIC
; #@! TA.AperFunction,Plated,PTH,ComponentDrill
T1C1.000
; #@! TA.AperFunction,NonPlated,NPTH,ComponentDrill
T2C3.200
%
G90
G05
T1
X10.0Y-5.0
X12.54Y-5.0
T2
X2.0Y2.0
M30
"""
    df = parse_excellon(kicad)
    assert len(df.holes) == 3
    assert df.holes[0].plated is True and df.holes[2].plated is False
    assert df.holes[1].x == pytest.approx(12.54) and df.holes[1].y == pytest.approx(-5.0)

    eagle = "M48\nINCH,TZ\nT01C0.0320\n%\nT01\nX10000Y5000\nM30\n"   # 2.4 inch, TZ
    df = parse_excellon(eagle)
    assert df.holes[0].x == pytest.approx(25.4) and df.holes[0].y == pytest.approx(12.7)
    assert df.holes[0].diameter == pytest.approx(0.8128)


# =========================================================================== #
# Dimensional: regra de cotas por formato
# =========================================================================== #
def test_placa_retangular_cota_pelos_lados(session_factory):
    s = session_factory(scenario_4l_smdtop_pth)
    assert s.result.shape == "Retangular"
    dims = s.board.shape.dimensions
    assert [(d.kind, round(d.value, 2), d.label) for d in dims] == [("H", 100.0, "X"), ("V", 80.0, "Y")]


def test_placa_cantos_arredondados_continua_retangular(session_factory):
    s = session_factory(scenario_2l_smd_both)
    assert s.result.shape == "Retangular"
    assert s.result.real_area_cm2 < s.result.area_cm2       # cantos com raio 3 mm


def test_placa_em_L_cota_pelos_lados_de_maior_comprimento(session_factory):
    s = session_factory(scenario_l_shape)
    r = s.result
    assert r.shape == "Irregular"
    dims = {d.label: d for d in s.board.shape.dimensions}
    assert dims["maior lado X"].value == pytest.approx(120.0, abs=0.01)
    assert dims["maior lado Y"].value == pytest.approx(90.0, abs=0.01)
    assert dims["maior lado X"].side == "bottom" and dims["maior lado Y"].side == "left"
    assert "total máx. X" not in dims          # o maior lado já é o envelope
    assert r.real_area_cm2 == pytest.approx(88.0, abs=0.01)   # 120x90 - 50x40
    assert r.area_cm2 == pytest.approx(108.0, abs=0.01)
    assert r.package_text == "140 x 110 x 25 mm"              # SMD 1 lado: Z 15 + 10


def test_formato_T_adiciona_cota_total():
    # T: barra superior 100 mm, haste 40 mm; maior lado horizontal = 100 (topo) = envelope;
    # lados verticais 30 e 50 -> maior lado Y (50) < envelope (80): cota total Y adicionada.
    g = GerberWriter("KO", "mm")
    g.polygon_outline([(30, 0), (70, 0), (70, 50), (100, 50), (100, 80), (0, 80), (0, 50), (30, 50)])
    from core.parsers import parse_gerber
    layer = parse_gerber(g.render())
    shape = analyze_outline(layer, layer.bbox(centerline=True))
    labels = {d.label: round(d.value, 2) for d in shape.dimensions}
    assert shape.shape == "Irregular"
    assert labels["maior lado X"] == 100.0 and labels["maior lado Y"] == 50.0
    assert labels["total máx. Y"] == 80.0 and "total máx. X" not in labels


def test_placa_circular_usa_envelope():
    g = GerberWriter("KO", "mm")
    import math
    pts = [(50 + 40 * math.cos(a / 36 * 2 * math.pi), 50 + 40 * math.sin(a / 36 * 2 * math.pi)) for a in range(36)]
    g.polygon_outline(pts)
    from core.parsers import parse_gerber
    layer = parse_gerber(g.render())
    shape = analyze_outline(layer, layer.bbox(centerline=True))
    labels = {d.label for d in shape.dimensions}
    assert shape.shape == "Irregular"
    assert {"total máx. X", "total máx. Y"} <= labels


# =========================================================================== #
# Embalagem
# =========================================================================== #
def test_embalagem_dimensoes_e_planificacao(session_factory):
    r = session_factory(scenario_4l_smdtop_pth).result
    b = box_dimensions(r)
    assert (b.length, b.width, b.height) == (120.0, 100.0, 35.0)
    assert b.blank_0201() == (2 * 120 + 2 * 100 + 30, 35 + 100)
    rows = dict(spec_rows(r, PackagingSpec(project="P", quantity=500)))
    assert rows["Medidas INTERNAS (C x L x A)"] == "120 x 100 x 35 mm"
    assert rows["Quantidade de caixas"] == "500"
    assert render_packaging(r, PackagingSpec(model="FEFCO 0427"), dpi=50).startswith(b"\x89PNG")


def test_embalagem_maior_medida_sempre_em_C(session_factory):
    s = session_factory(scenario_1l_pth_only)
    r = s.reevaluate(AnalysisOptions())
    r.board_x, r.board_y = 30.0, 50.0
    r.package = (50.0, 70.0, 35.0)
    b = box_dimensions(r)
    assert (b.length, b.width) == (70.0, 50.0) and (b.pcb_x, b.pcb_y) == (50.0, 30.0)


def test_pdf_embalagem_avulso(session_factory, tmp_path):
    r = session_factory(scenario_2l_smd_both).result
    rep = generate_package_pdf(tmp_path / "emb.pdf", r, PackagingSpec(project="SMD", quantity=1000), dpi=60)
    assert rep.path.exists() and rep.pages == 1


def test_ui_smoke(tmp_path):
    """Abre a janela em modo offscreen, importa o cenário 1 e verifica o resumo."""
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication

    from ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    zpath = scenario_4l_smdtop_pth(tmp_path / "ui.zip")
    w.load_source(str(zpath))
    for _ in range(100):
        loop = QEventLoop()
        QTimer.singleShot(100, loop.quit)
        loop.exec()
        if w.result is not None:
            break
    assert w.result is not None
    assert w.lbl_status_bot.text() == "LADO BOT: SEM MONTAGEM"
    assert w.lbl_package.text() == "120 x 100 x 35 mm"
    assert w.tbl_files.rowCount() == 10
    assert w.spin_layers.value() == 4
    assert w.tabs.count() == 6 and "Embalagem" in w.tabs.tabText(4) and "Tempos" in w.tabs.tabText(5)
    assert w.count_spins["pth"].value() == w.result.comp_pth
    assert w.tbl_times.rowCount() > 0
    assert w.sum["shape"].text() == "Retangular"
    w.close()
    app.processEvents()
