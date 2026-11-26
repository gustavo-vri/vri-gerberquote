"""Estimativa de tempos de montagem para cotação.

* **SMD** — linha automática: impressora de pasta + pick & place **Fuji AIM** + forno de
  refusão. O tempo por placa e por lado é o *takt* da linha (a etapa mais lenta entre
  impressão, pick & place e passagem no forno) + inspeção. O setup por lote considera
  programação, um alimentador (feeder) por tipo de componente e a primeira peça.
* **PTH** — montagem manual seguindo IPC-A-610 (aceitabilidade) e IPC J-STD-001 (soldagem):
  preparo/formação de terminais, inserção, solda manual por junta, corte de terminais e
  inspeção visual.

Todos os valores padrão são **referências iniciais** e devem ser calibrados com os tempos
reais da VRI (tela "Parâmetros de tempo"). Os parâmetros ficam salvos em
``~/.vri_gerberquote/tempos.json``.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from core.components import CAT_CHIP, CAT_IC, CAT_SMALL, ComponentCount

CONFIG_DIR = Path.home() / ".vri_gerberquote"
CONFIG_FILE = CONFIG_DIR / "tempos.json"


@dataclass
class TimeParams:
    # --- SMD: Fuji AIM (tempo EFETIVO por componente, já incluindo trocas de bico e visão) ---
    machine: str = "Fuji AIM"
    t_chip_s: float = 0.55          # chips 2 terminais (0402…1206, diodos, LEDs)
    t_small_s: float = 0.90         # SOT, SOD, pequenos 3–6 terminais
    t_ic_s: float = 3.00            # CIs multipinos, conectores SMD, BGA (bico lento/visão)
    t_handling_side_s: float = 20.0  # entrada, leitura de fiduciais e saída da placa
    t_print_side_s: float = 30.0     # ciclo da impressora de pasta (stencil)
    conveyor_m_min: float = 0.8      # velocidade da esteira do forno de refusão
    board_gap_mm: float = 100.0      # espaçamento entre placas no forno
    oven_length_m: float = 3.0       # comprimento aquecido (informativo: tempo de passagem)
    t_inspect_smd_s: float = 0.30    # inspeção visual/AOI por componente SMD
    setup_program_min: float = 30.0  # programa, offsets e otimização (por lado montado)
    setup_feeder_min: float = 3.0    # por tipo de componente (alimentador)
    first_article_min: float = 15.0  # primeira peça / liberação (por lado montado)
    # --- PTH: montagem manual IPC-A-610 / J-STD-001 ---
    t_pth_prep_s: float = 4.0        # preparo e formação de terminais, por componente
    t_pth_insert_s: float = 6.0      # inserção, por componente
    t_pth_solder_joint_s: float = 4.0  # solda manual com ferro, por junta
    t_pth_cut_joint_s: float = 1.5   # corte de terminal e limpeza, por junta
    t_pth_inspect_s: float = 3.0     # inspeção IPC-A-610, por componente
    t_pth_handling_s: float = 30.0   # manuseio da placa na bancada
    setup_pth_min: float = 20.0      # separação de kit / preparação do posto, por lote


LABELS = {
    "machine": "Máquina SMD",
    "t_chip_s": "Chip 2 terminais (s/comp.)",
    "t_small_s": "SOT/pequeno 3–6 terminais (s/comp.)",
    "t_ic_s": "CI multipinos/BGA (s/comp.)",
    "t_handling_side_s": "Manuseio + fiduciais (s/lado)",
    "t_print_side_s": "Impressão de pasta (s/lado)",
    "conveyor_m_min": "Esteira do forno (m/min)",
    "board_gap_mm": "Espaço entre placas no forno (mm)",
    "oven_length_m": "Comprimento do forno (m)",
    "t_inspect_smd_s": "Inspeção SMD (s/comp.)",
    "setup_program_min": "Setup: programa (min/lado)",
    "setup_feeder_min": "Setup: por tipo/feeder (min)",
    "first_article_min": "Primeira peça (min/lado)",
    "t_pth_prep_s": "Preparo/formação (s/comp.)",
    "t_pth_insert_s": "Inserção (s/comp.)",
    "t_pth_solder_joint_s": "Solda manual (s/junta)",
    "t_pth_cut_joint_s": "Corte + limpeza (s/junta)",
    "t_pth_inspect_s": "Inspeção IPC-A-610 (s/comp.)",
    "t_pth_handling_s": "Manuseio na bancada (s/placa)",
    "setup_pth_min": "Setup PTH (min/lote)",
}


def load_params(path: Path = CONFIG_FILE) -> TimeParams:
    p = TimeParams()
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for f in fields(TimeParams):
            if f.name in data:
                setattr(p, f.name, type(getattr(p, f.name))(data[f.name]))
    except (OSError, ValueError, TypeError):
        pass
    return p


def save_params(params: TimeParams, path: Path = CONFIG_FILE) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(params), indent=2, ensure_ascii=False), encoding="utf-8")


@dataclass
class SideTime:
    side: str
    components: int = 0
    by_category: dict[str, int] = field(default_factory=dict)
    types: int = 0
    placement_s: float = 0.0
    pnp_cycle_s: float = 0.0
    print_s: float = 0.0
    reflow_takt_s: float = 0.0
    takt_s: float = 0.0
    bottleneck: str = ""
    inspect_s: float = 0.0
    unit_s: float = 0.0
    setup_min: float = 0.0


@dataclass
class TimeEstimate:
    smd_sides: list[SideTime]
    pth_components: int
    pth_joints: int
    pth_unit_s: float
    pth_setup_min: float
    smd_unit_s: float
    smd_setup_min: float
    lot_qty: int
    oven_pass_min: float
    lines: list[tuple[str, str]] = field(default_factory=list)

    @property
    def unit_total_s(self) -> float:
        return self.smd_unit_s + self.pth_unit_s

    @property
    def setup_total_min(self) -> float:
        return self.smd_setup_min + self.pth_setup_min

    @property
    def lot_smd_h(self) -> float:
        return (self.smd_setup_min * 60 + self.lot_qty * self.smd_unit_s) / 3600

    @property
    def lot_pth_h(self) -> float:
        return (self.pth_setup_min * 60 + self.lot_qty * self.pth_unit_s) / 3600

    @property
    def lot_total_h(self) -> float:
        return self.lot_smd_h + self.lot_pth_h


def fmt_time(seconds: float) -> str:
    s = int(round(seconds))
    if s < 60:
        return f"{s} s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m} min {s:02d} s"
    h, m = divmod(m, 60)
    return f"{h} h {m:02d} min"


def estimate_times(comps: ComponentCount, board_x: float, board_y: float, params: TimeParams,
                   lot_qty: int = 1, smd_override: dict[str, int] | None = None,
                   pth_override: int | None = None) -> TimeEstimate:
    p = params
    speed_mm_s = max(p.conveyor_m_min, 0.01) * 1000 / 60
    sides: list[SideTime] = []
    for side in ("TOP", "BOT"):
        st = SideTime(side)
        cats = comps.by_category("SMD", side)
        auto_n = sum(cats.values())
        n = auto_n
        if smd_override and smd_override.get(side) is not None:
            n = smd_override[side]
            if auto_n:  # mantém a proporção entre categorias
                cats = {k: v * n / auto_n for k, v in cats.items()}
            else:
                cats = {CAT_CHIP: float(n)}
        st.components = n
        st.by_category = {k: int(round(v)) for k, v in cats.items()}
        if n > 0:
            st.types = max(1, comps.distinct_types("SMD", side)) if auto_n else max(1, n // 4)
            st.placement_s = (cats.get(CAT_CHIP, 0) * p.t_chip_s + cats.get(CAT_SMALL, 0) * p.t_small_s
                              + cats.get(CAT_IC, 0) * p.t_ic_s)
            st.pnp_cycle_s = st.placement_s + p.t_handling_side_s
            st.print_s = p.t_print_side_s
            st.reflow_takt_s = (max(board_x, board_y) + p.board_gap_mm) / speed_mm_s
            options = {"Impressão de pasta": st.print_s, f"Pick & place ({p.machine})": st.pnp_cycle_s,
                       "Forno de refusão": st.reflow_takt_s}
            st.bottleneck = max(options, key=options.get)
            st.takt_s = options[st.bottleneck]
            st.inspect_s = n * p.t_inspect_smd_s
            st.unit_s = st.takt_s + st.inspect_s
            st.setup_min = p.setup_program_min + p.first_article_min + st.types * p.setup_feeder_min
        sides.append(st)

    pth_n = comps.pth if pth_override is None else pth_override
    joints = comps.terminals("PTH")
    if pth_override is not None and comps.pth:
        joints = round(joints * pth_override / comps.pth)
    elif pth_override is not None:
        joints = 2 * pth_override
    pth_unit = 0.0
    if pth_n:
        pth_unit = (pth_n * (p.t_pth_prep_s + p.t_pth_insert_s + p.t_pth_inspect_s)
                    + joints * (p.t_pth_solder_joint_s + p.t_pth_cut_joint_s) + p.t_pth_handling_s)
    est = TimeEstimate(
        smd_sides=sides, pth_components=pth_n, pth_joints=joints, pth_unit_s=pth_unit,
        pth_setup_min=p.setup_pth_min if pth_n else 0.0,
        smd_unit_s=sum(s.unit_s for s in sides), smd_setup_min=sum(s.setup_min for s in sides),
        lot_qty=max(1, int(lot_qty)), oven_pass_min=p.oven_length_m / max(p.conveyor_m_min, 0.01),
    )
    return est


def time_rows(est: TimeEstimate, params: TimeParams) -> list[tuple[str, str, str]]:
    """Linhas de detalhamento (etapa, base de cálculo, tempo) para tela e PDF."""
    rows: list[tuple[str, str, str]] = []
    for st in est.smd_sides:
        if not st.components:
            continue
        cats = ", ".join(f"{v} {k.split(' (')[0].lower()}" for k, v in st.by_category.items() if v)
        rows.append((f"SMD {st.side} — componentes", cats, f"{st.components} comp."))
        rows.append((f"SMD {st.side} — pick & place ({params.machine})",
                     f"colocação {fmt_time(st.placement_s)} + manuseio {fmt_time(params.t_handling_side_s)}",
                     fmt_time(st.pnp_cycle_s)))
        rows.append((f"SMD {st.side} — impressão de pasta", "ciclo da impressora", fmt_time(st.print_s)))
        rows.append((f"SMD {st.side} — forno de refusão (takt)",
                     f"(placa + {params.board_gap_mm:.0f} mm) ÷ {params.conveyor_m_min:.2f} m/min",
                     fmt_time(st.reflow_takt_s)))
        rows.append((f"SMD {st.side} — takt da linha", f"gargalo: {st.bottleneck}", fmt_time(st.takt_s)))
        rows.append((f"SMD {st.side} — inspeção", f"{st.components} × {params.t_inspect_smd_s:g} s",
                     fmt_time(st.inspect_s)))
        rows.append((f"SMD {st.side} — tempo por placa", "takt + inspeção", fmt_time(st.unit_s)))
        rows.append((f"SMD {st.side} — setup do lote",
                     f"programa {params.setup_program_min:g} + {st.types} tipos × {params.setup_feeder_min:g} "
                     f"+ 1ª peça {params.first_article_min:g} min", f"{st.setup_min:.0f} min"))
    if est.pth_components:
        p = params
        rows.append(("PTH — componentes (manual, IPC-A-610 / J-STD-001)",
                     f"{est.pth_components} comp., {est.pth_joints} juntas de solda", f"{est.pth_components} comp."))
        rows.append(("PTH — preparo + inserção + inspeção",
                     f"{est.pth_components} × ({p.t_pth_prep_s:g} + {p.t_pth_insert_s:g} + {p.t_pth_inspect_s:g}) s",
                     fmt_time(est.pth_components * (p.t_pth_prep_s + p.t_pth_insert_s + p.t_pth_inspect_s))))
        rows.append(("PTH — solda manual + corte de terminais",
                     f"{est.pth_joints} juntas × ({p.t_pth_solder_joint_s:g} + {p.t_pth_cut_joint_s:g}) s",
                     fmt_time(est.pth_joints * (p.t_pth_solder_joint_s + p.t_pth_cut_joint_s))))
        rows.append(("PTH — manuseio na bancada", "por placa", fmt_time(p.t_pth_handling_s)))
        rows.append(("PTH — tempo por placa", "", fmt_time(est.pth_unit_s)))
        rows.append(("PTH — setup do lote", "kit / preparação do posto", f"{est.pth_setup_min:.0f} min"))
    rows.append(("TOTAL por placa (SMD + PTH)", "", fmt_time(est.unit_total_s)))
    rows.append((f"TOTAL do lote ({est.lot_qty} placas)",
                 f"setup {est.setup_total_min:.0f} min + {est.lot_qty} × {fmt_time(est.unit_total_s)}",
                 f"{est.lot_total_h:.2f} h".replace(".", ",")))
    if any(s.components for s in est.smd_sides):
        rows.append(("Passagem no forno (lead time, informativo)",
                     f"{params.oven_length_m:g} m ÷ {params.conveyor_m_min:g} m/min", f"{est.oven_pass_min:.1f} min"))
    return rows
