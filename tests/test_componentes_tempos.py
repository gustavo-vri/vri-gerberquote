"""Contagem de componentes (não pads), Pick & Place, tabela de furação Altium e tempos."""
from __future__ import annotations

import pytest

from core.analyzer import AnalysisOptions, analyze_project
from core.classifier import LayerType
from core.components import CAT_CHIP, CAT_IC, CAT_SMALL
from core.pnp import parse_pnp
from core.timing import TimeParams, estimate_times, load_params, save_params, time_rows
from render.gerber_renderer import render_dim_drill
from tests.synthetic import MIXED_EXPECTED, scenario_4l_smdtop_pth, scenario_mixed


@pytest.fixture
def mixed(tmp_path):
    def make(with_pnp, options=None):
        s = analyze_project(scenario_mixed(tmp_path / f"m{int(with_pnp)}.zip", with_pnp), options)
        return s
    return make


# --------------------------------------------------------------------------- #
# Componentes
# --------------------------------------------------------------------------- #
def test_estimativa_geometrica_conta_componentes_e_nao_pads(mixed):
    s = mixed(False)
    c = s.result.components
    assert not c.exact
    assert (c.smd_top, c.smd_bot, c.pth) == (MIXED_EXPECTED["smd_top"], MIXED_EXPECTED["smd_bot"],
                                              MIXED_EXPECTED["pth"])
    assert s.result.smd_top > c.smd_top          # pads >> componentes
    cats = c.by_category("SMD", "TOP")
    assert cats[CAT_CHIP] == 11                  # 6 resistores + 4 capacitores + 1 eletrolítico
    assert cats[CAT_SMALL] == 1                  # SOT-23
    assert cats[CAT_IC] == 3                     # SOIC-8, LQFP-32, QFN-16 (+pad térmico)
    terms = sorted(k.terminals for k in c.components if k.kind == "SMD" and k.category == CAT_IC)
    assert terms == [8, 17, 32]


def test_estimativa_pth_dip_barra_axiais_radial_borne_to220(mixed):
    c = mixed(False).result.components
    pth = sorted(k.terminals for k in c.components if k.kind == "PTH")
    assert pth == [2, 2, 2, 3, 3, 6, 8]
    # os dois axiais paralelos são pareados pelo corpo na serigrafia (não na vertical)
    axial = sorted((round(k.x, 1), round(k.y, 1)) for k in c.components if k.kind == "PTH" and k.terminals == 2
                   and k.x < 30)
    assert axial == [(15.1, 62.0), (15.1, 67.0)]


def test_pick_and_place_contagem_exata(mixed):
    s = mixed(True)
    t = {c.source.relpath: c.layer_type for c in s.classified}
    assert t["Assembly/Pick Place for Mista.txt"] == LayerType.PICK_PLACE
    c = s.result.components
    assert c.exact and "Pick & Place" in c.source
    assert (c.smd_top, c.smd_bot, c.pth) == (15, 3, 7)
    assert set(c.excluded) == {"FID1", "MH1"}
    by_ref = {k.designator: k for k in c.components}
    assert by_ref["U2"].terminals == 32 and by_ref["U2"].category == CAT_IC
    assert by_ref["R1"].terminals == 2 and by_ref["R1"].kind == "SMD"
    assert by_ref["U4"].kind == "PTH" and by_ref["U4"].terminals == 8
    assert by_ref["R20"].side == "BOT"


@pytest.mark.parametrize("text,name,expected", [
    ('Ref,Val,Package,PosX,PosY,Rot,Side\n"R1","10k","R_0603",10.0,5.0,0,top\n'
     '"C1","1u","C_0805",12,5,90,bottom\n', "proj-all-pos.csv", [("R1", "TOP"), ("C1", "BOT")]),
    ("### Footprint positions\n## Unit = mm, Angle = deg.\n# Ref     Val    Package        PosX   PosY  Rot  Side\n"
     "U1        MCU    LQFP-48        50.0   40.0  0.0  top\n", "proj.pos", [("U1", "TOP")]),
    ("Designator Footprint Mid X Mid Y Ref X Ref Y Pad X Pad Y TB Rotation Comment\n\n"
     "R5 0603 1000mil 500mil 1000mil 500mil 1030mil 500mil B 0 10k\n", "Pick Place.txt", [("R5", "BOT")]),
    ("R1 10.16 20.32 90 10k R0603\nIC1 30 30 0 ATMEGA TQFP32\n", "board.mnt", [("R1", "TOP"), ("IC1", "TOP")]),
])
def test_formatos_pick_and_place(text, name, expected):
    pf = parse_pnp(text, name)
    assert [(p.designator, p.side) for p in pf.placements] == expected


def test_pnp_legado_altium_em_mil():
    pf = parse_pnp("Designator Footprint Mid X Mid Y TB\nR5 0603 1000mil 500mil T\n", "x.txt")
    assert pf.placements[0].x == pytest.approx(25.4) and pf.placements[0].y == pytest.approx(12.7)


def test_override_manual_de_componentes(mixed):
    s = mixed(False)
    r = s.reevaluate(AnalysisOptions(smd_top_override=30, pth_override=10))
    assert r.comp_smd_top == 30 and r.comp_pth == 10
    assert r.times.smd_sides[0].placement_s > 0


# --------------------------------------------------------------------------- #
# Tabela de furação padrão Altium
# --------------------------------------------------------------------------- #
def test_tabela_furacao_padrao_altium(tmp_path):
    s = analyze_project(scenario_4l_smdtop_pth(tmp_path / "a.zip"))
    g = s.result.drill_groups
    assert [x.diameter for x in g] == sorted(x.diameter for x in g)        # ordenado por diâmetro
    assert g[0].hole_size_text == "0.300mm (11.81mil)"
    assert [x.plated_text for x in g] == ["PTH", "PTH", "PTH", "NPTH"]
    assert all(x.hole_type_text == "Round" for x in g)
    assert render_dim_drill(s.board, s.result, dpi=50).startswith(b"\x89PNG")


# --------------------------------------------------------------------------- #
# Tempos
# --------------------------------------------------------------------------- #
def test_tempos_smd_e_pth(mixed):
    r = mixed(True, AnalysisOptions(lot_qty=100)).result
    t = r.times
    p = TimeParams()
    top = t.smd_sides[0]
    assert top.placement_s == pytest.approx(11 * p.t_chip_s + 1 * p.t_small_s + 3 * p.t_ic_s)
    assert top.takt_s == max(top.pnp_cycle_s, p.t_print_side_s, top.reflow_takt_s)
    assert top.reflow_takt_s == pytest.approx((100 + p.board_gap_mm) / (p.conveyor_m_min * 1000 / 60))
    assert t.pth_components == 7 and t.pth_joints == 26
    assert t.pth_unit_s == pytest.approx(7 * (4 + 6 + 3) + 26 * (4 + 1.5) + 30)
    assert t.lot_total_h == pytest.approx((t.setup_total_min * 60 + 100 * t.unit_total_s) / 3600)
    labels = [a for a, _, _ in time_rows(t, p)]
    assert "TOTAL do lote (100 placas)" in labels


def test_tempos_sem_montagem():
    from core.components import ComponentCount

    t = estimate_times(ComponentCount(), 50, 50, TimeParams(), 10)
    assert t.unit_total_s == 0 and t.setup_total_min == 0


def test_parametros_salvos(tmp_path):
    f = tmp_path / "tempos.json"
    p = TimeParams(t_chip_s=0.4, machine="Fuji AIM H08")
    save_params(p, f)
    q = load_params(f)
    assert q.t_chip_s == 0.4 and q.machine == "Fuji AIM H08"
    assert load_params(tmp_path / "nao_existe.json") == TimeParams()


# --------------------------------------------------------------------------- #
# Casos reportados pela VRI (placa Deltronix PCB0032): PTH separados pelo contorno da serigrafia
# --------------------------------------------------------------------------- #
def test_casos_pth_reportados(tmp_path):
    from tests.synthetic import PTH_CASES_EXPECTED, scenario_pth_cases

    s = analyze_project(scenario_pth_cases(tmp_path / "casos.zip"))
    c = s.result.components
    pth = [k for k in c.components if k.kind == "PTH"]
    assert c.pth == PTH_CASES_EXPECTED["pth"]

    def at(x, y):
        return [k for k in pth if k.bbox[0] - 0.5 <= x <= k.bbox[2] + 0.5 and k.bbox[1] - 0.5 <= y <= k.bbox[3] + 0.5]

    # TP1..TP7: 7 componentes de 3 pinos (contornos empilhados que compartilham um lado)
    assert sorted(k.terminals for k in pth if 39 <= k.x <= 46 and k.y < 45) == [3] * 7
    # C60 e C51 (radiais lado a lado) são 2 componentes; C52 e C61 também
    assert len({id(k) for k in at(10.0, 40.0) + at(16.5, 40.0)}) == 2
    assert len({id(k) for k in at(30.0, 60.0) + at(36.5, 60.0)}) == 2
    # Y1: 2 terminais; o furo de passagem ao lado (dentro de nenhum contorno) não entra
    y1 = at(60.0, 20.0)
    assert len(y1) == 1 and y1[0].terminals == 2
    # furos de passagem junto ao SMD C41, isolado (C45) e par sem contorno: não são componentes
    for x, y in ((70.0, 42.6), (75.0, 45.0), (80.0, 50.0), (85.0, 50.0), (62.4, 24.6)):
        assert not at(x, y)
    # P13..P16 (4 pinos encostados) e P6 (2x5) são 5 componentes distintos
    assert sorted(k.terminals for k in pth if k.y > 60) == [4, 4, 4, 4, 10]
    assert any("sem componente" in w for w in c.warnings)


def test_barra_2x3_em_contornos_de_2_pinos_vira_um_componente():
    from core.components import _merge_continuous_grids
    from core.analyzer import Hole

    holes = [Hole(x, y, 1.0, True, False) for y in (0.0, 2.54, 5.08) for x in (0.0, 2.54)]
    groups = [([0, 1], "TOP"), ([2, 3], "TOP"), ([4, 5], "TOP")]
    assert [len(g) for g, _ in _merge_continuous_grids(holes, groups)] == [6]
    # fileiras a 5 mm (como TP1..TP7) continuam separadas
    holes2 = [Hole(x, y, 1.0, True, False) for y in (0.0, 5.0) for x in (0.0, 2.54, 5.08)]
    assert len(_merge_continuous_grids(holes2, [([0, 1, 2], "TOP"), ([3, 4, 5], "TOP")])) == 2
