"""Análise técnico-comercial da placa.

Fluxo em duas fases para a interface responder rápido:

* :meth:`ProjectAnalyzer.load` — faz o *parse* (custoso, com cache por arquivo);
* :func:`evaluate` — aplica as opções do orçamentista (lado PTH, overrides de
  layers/altura) e calcula contagens, status de montagem e embalagem.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from core.classifier import ClassifiedFile, LayerType, classify_files, count_layers
from core.extractor import ExtractedProject, extract_project
from core.components import ComponentCount, count_components
from core.geometry import OutlineShape, analyze_outline
from core.pnp import Placement, parse_pnp
from core.timing import TimeEstimate, TimeParams, estimate_times
from core.parsers import DrillFile, GerberLayer, parse_excellon, parse_gerber

# Regras de altura estimada de montagem (Z, em mm)
HEIGHT_BARE = 10.0
HEIGHT_SMD_ONE_SIDE = 15.0
HEIGHT_SMD_TWO_SIDES = 20.0
HEIGHT_PTH_ONE_SIDE = 25.0
HEIGHT_PTH_PLUS_OPPOSITE = 30.0

# Margens de embalagem (proteção ESD / plástico bolha)
PACKAGE_MARGIN_X = 20.0
PACKAGE_MARGIN_Y = 20.0
PACKAGE_MARGIN_Z = 10.0

DEFAULT_VIA_MAX_DIAMETER = 0.5  # mm: furos metalizados <= este valor são vias


# --------------------------------------------------------------------------- #
# Estruturas
# --------------------------------------------------------------------------- #
@dataclass
class AnalysisOptions:
    pth_top: bool = True
    pth_bot: bool = False
    layers_override: int | None = None
    height_override: float | None = None
    via_max_diameter: float = DEFAULT_VIA_MAX_DIAMETER
    # Componentes e tempos
    time_params: TimeParams | None = None
    lot_qty: int = 100
    smd_top_override: int | None = None
    smd_bot_override: int | None = None
    pth_override: int | None = None


@dataclass
class Hole:
    x: float
    y: float
    diameter: float
    plated: bool
    is_via: bool
    slot: bool = False

    @property
    def kind(self) -> str:
        if not self.plated:
            return "NPTH"
        return "VIA" if self.is_via else "PTH"


@dataclass
class DrillGroup:
    diameter: float
    kind: str          # "PTH" | "VIA" | "NPTH"
    count: int
    slot: bool = False

    @property
    def label(self) -> str:
        return {"PTH": "PTH (componente)", "VIA": "PTH (via)", "NPTH": "NPTH (mecânico)"}[self.kind]

    # --- colunas no padrão "Drill Table" do Altium -------------------------
    @property
    def plated_text(self) -> str:
        return "NPTH" if self.kind == "NPTH" else "PTH"

    @property
    def hole_size_text(self) -> str:
        return f"{self.diameter:.3f}mm ({self.diameter / 0.0254:.2f}mil)"

    @property
    def hole_type_text(self) -> str:
        return "Slot" if self.slot else "Round"


@dataclass
class BoardData:
    """Geometria já interpretada, usada pelos renderizadores."""

    outline: GerberLayer | None = None
    outline_name: str = ""
    copper_top: GerberLayer | None = None
    copper_bot: GerberLayer | None = None
    inner: list[GerberLayer] = field(default_factory=list)
    silk_top: GerberLayer | None = None
    silk_bot: GerberLayer | None = None
    paste_top: GerberLayer | None = None
    paste_bot: GerberLayer | None = None
    mask_top: GerberLayer | None = None
    mask_bot: GerberLayer | None = None
    drills: list[tuple[DrillFile, LayerType]] = field(default_factory=list)
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    bbox_from_outline: bool = False
    copper_layer_count: int = 0
    warnings: list[str] = field(default_factory=list)
    holes: list[Hole] = field(default_factory=list)  # preenchido em evaluate()
    shape: OutlineShape | None = None                 # polígono, formato e cotas
    placements: list[Placement] = field(default_factory=list)   # Pick & Place (se houver)
    pnp_names: list[str] = field(default_factory=list)
    pnp_excluded: list[str] = field(default_factory=list)

    @property
    def width(self) -> float:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]


@dataclass
class SideStatus:
    side: str            # "TOP" | "BOT"
    smd_pads: int
    pth_holes: int       # PTH de componentes inseridos por este lado
    message: str

    @property
    def has_smd(self) -> bool:
        return self.smd_pads > 0

    @property
    def has_pth(self) -> bool:
        return self.pth_holes > 0

    @property
    def has_assembly(self) -> bool:
        return self.has_smd or self.has_pth

    @property
    def has_view(self) -> bool:
        """Desenho de montagem só é gerado se o lado tiver algum componente."""
        return self.has_assembly

    @property
    def is_partial(self) -> bool:
        return self.has_assembly and not (self.has_smd and self.has_pth)


@dataclass
class AnalysisResult:
    layers: int
    layers_auto: int
    board_x: float
    board_y: float
    area_cm2: float
    smd_top: int
    smd_bot: int
    smd_top_source: str
    smd_bot_source: str
    pth_total: int
    pth_component: int
    via_count: int
    npth_count: int
    drill_groups: list[DrillGroup]
    top: SideStatus
    bot: SideStatus
    assembly_height_auto: float
    assembly_height: float
    package: tuple[float, float, float]
    warnings: list[str]
    shape: str = "Indefinido"
    real_area_cm2: float = 0.0
    components: ComponentCount | None = None
    times: TimeEstimate | None = None

    @property
    def comp_smd_top(self) -> int:
        return self.times.smd_sides[0].components if self.times else 0

    @property
    def comp_smd_bot(self) -> int:
        return self.times.smd_sides[1].components if self.times else 0

    @property
    def comp_pth(self) -> int:
        return self.times.pth_components if self.times else 0

    @property
    def status_top(self) -> str:
        return self.top.message

    @property
    def status_bot(self) -> str:
        return self.bot.message

    @property
    def package_text(self) -> str:
        x, y, z = (math.ceil(v - 0.01) for v in self.package)  # nunca arredonda para menos
        return f"{x} x {y} x {z} mm"

    @property
    def board_text(self) -> str:
        return f"{fmt_mm(self.board_x)} x {fmt_mm(self.board_y)} mm"


def fmt_mm(value: float, decimals: int = 2) -> str:
    """Formata número no padrão brasileiro (vírgula decimal)."""
    txt = f"{value:,.{decimals}f}"
    return txt.replace(",", "§").replace(".", ",").replace("§", ".")


# --------------------------------------------------------------------------- #
# Regras de negócio
# --------------------------------------------------------------------------- #
def side_message(side: str, smd: int, pth: int) -> str:
    if not smd and not pth:
        return f"LADO {side}: SEM MONTAGEM"
    if smd and not pth:
        return f"LADO {side}: SEM MONTAGEM PTH (APENAS SMD)"
    if pth and not smd:
        return f"LADO {side}: SEM MONTAGEM SMD (APENAS PTH)"
    return f"LADO {side}: MONTAGEM SMD + PTH"


def estimate_height(top: SideStatus, bot: SideStatus) -> float:
    """Altura estimada (Z) conforme a tecnologia detectada."""
    if not top.has_assembly and not bot.has_assembly:
        return HEIGHT_BARE
    if top.has_pth or bot.has_pth:
        opposite = (top.has_pth and bot.has_assembly) or (bot.has_pth and top.has_assembly)
        return HEIGHT_PTH_PLUS_OPPOSITE if opposite else HEIGHT_PTH_ONE_SIDE
    sides = int(top.has_smd) + int(bot.has_smd)
    return HEIGHT_SMD_TWO_SIDES if sides == 2 else HEIGHT_SMD_ONE_SIDE


def package_dimensions(board_x: float, board_y: float, height: float) -> tuple[float, float, float]:
    return (board_x + PACKAGE_MARGIN_X, board_y + PACKAGE_MARGIN_Y, height + PACKAGE_MARGIN_Z)


# --------------------------------------------------------------------------- #
# Carga / parse
# --------------------------------------------------------------------------- #
def _merge(layers: list[GerberLayer]) -> GerberLayer | None:
    if not layers:
        return None
    if len(layers) == 1:
        return layers[0]
    out = GerberLayer(name=" + ".join(l.name for l in layers))
    for l in layers:
        out.flashes += l.flashes
        out.tracks += l.tracks
        out.regions += l.regions
    return out


def _union_bbox(boxes):
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _area(b):
    return (b[2] - b[0]) * (b[3] - b[1])


class ProjectAnalyzer:
    """Mantém cache de arquivos já interpretados (troca de tipo na UI é instantânea)."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], object] = {}

    def _parse(self, cf: ClassifiedFile):
        key = (str(cf.source.path), cf.source.fmt)
        if key not in self._cache:
            text = cf.source.read_text()
            if cf.source.fmt == "pnp":
                self._cache[key] = parse_pnp(text, cf.name)
            elif cf.source.fmt == "excellon":
                self._cache[key] = parse_excellon(text, cf.name)
            else:
                self._cache[key] = parse_gerber(text, cf.name)
        return self._cache[key]

    def load(self, classified: list[ClassifiedFile]) -> BoardData:
        board = BoardData()
        by_type: dict[LayerType, list[GerberLayer]] = {}
        for cf in classified:
            lt = cf.layer_type
            if lt in (LayerType.IGNORE, LayerType.MECHANICAL):
                continue
            parsed = self._parse(cf)
            if lt == LayerType.PICK_PLACE:
                if hasattr(parsed, "placements"):
                    board.placements += parsed.placements
                    board.pnp_names.append(cf.name)
                    board.pnp_excluded += parsed.excluded
                    board.warnings += [f"{cf.name}: {w}" for w in parsed.warnings]
                continue
            if lt.is_drill:
                if isinstance(parsed, DrillFile):
                    board.drills.append((parsed, lt))
                else:
                    board.warnings.append(f"{cf.name}: marcado como Drill mas não é Excellon.")
                continue
            if not isinstance(parsed, GerberLayer):
                board.warnings.append(f"{cf.name}: arquivo não-Gerber marcado como camada Gerber.")
                continue
            by_type.setdefault(lt, []).append(parsed)
            for w in parsed.warnings:
                board.warnings.append(f"{cf.name}: {w}")

        board.copper_top = _merge(by_type.get(LayerType.TOP_COPPER, []))
        board.copper_bot = _merge(by_type.get(LayerType.BOTTOM_COPPER, []))
        board.inner = by_type.get(LayerType.INNER, [])
        board.silk_top = _merge(by_type.get(LayerType.TOP_SILK, []))
        board.silk_bot = _merge(by_type.get(LayerType.BOTTOM_SILK, []))
        board.paste_top = _merge(by_type.get(LayerType.TOP_PASTE, []))
        board.paste_bot = _merge(by_type.get(LayerType.BOTTOM_PASTE, []))
        board.mask_top = _merge(by_type.get(LayerType.TOP_MASK, []))
        board.mask_bot = _merge(by_type.get(LayerType.BOTTOM_MASK, []))
        board.copper_layer_count = count_layers(classified)

        copper_bbox = _union_bbox([l.bbox() for l in
                                   [board.copper_top, board.copper_bot, *board.inner] if l])

        # Contorno: se houver mais de um candidato (ex.: GKO + GM1 com cotas),
        # escolhe o menor que ainda envolve o cobre.
        candidates = [(l, l.bbox(centerline=True)) for l in by_type.get(LayerType.OUTLINE, [])]
        candidates = [(l, b) for l, b in candidates if b and _area(b) > 0]
        if candidates:
            def covers(b):
                if not copper_bbox:
                    return True
                tol = 0.05 * max(copper_bbox[2] - copper_bbox[0], copper_bbox[3] - copper_bbox[1])
                return (b[0] <= copper_bbox[0] + tol and b[1] <= copper_bbox[1] + tol and
                        b[2] >= copper_bbox[2] - tol and b[3] >= copper_bbox[3] - tol)
            good = [c for c in candidates if covers(c[1])] or candidates
            layer, bbox = min(good, key=lambda c: _area(c[1]))
            board.outline, board.outline_name, board.bbox = layer, layer.name, bbox
            board.bbox_from_outline = True
        else:
            fallback = _union_bbox([copper_bbox] + [l.bbox() for l in
                                   [board.paste_top, board.paste_bot, board.silk_top, board.silk_bot] if l])
            holes_bbox = None
            pts = [(h.x, h.y) for d, _ in board.drills for h in d.holes]
            if pts:
                a = np.array(pts)
                holes_bbox = (a[:, 0].min(), a[:, 1].min(), a[:, 0].max(), a[:, 1].max())
            fallback = _union_bbox([fallback, holes_bbox])
            board.bbox = fallback or (0.0, 0.0, 0.0, 0.0)
            board.warnings.append("Contorno (Outline) não encontrado: dimensões estimadas "
                                  "pelo envelope do cobre/furação. Confirme com o cliente.")
        board.shape = analyze_outline(board.outline, board.bbox)
        if board.outline is not None and board.shape.polygon is None:
            board.warnings.append("Contorno aberto (traços não fecham): cotas pelo envelope X/Y.")
        return board


# --------------------------------------------------------------------------- #
# Avaliação
# --------------------------------------------------------------------------- #
def _resolve_holes(board: BoardData, via_max: float) -> list[Hole]:
    holes: list[Hole] = []
    for df, lt in board.drills:
        for h in df.holes:
            if lt == LayerType.DRILL_PTH:
                plated = True
            elif lt == LayerType.DRILL_NPTH:
                plated = False
            else:  # misto: ferramenta > cabeçalho > assume metalizado
                plated = h.plated if h.plated is not None else (
                    df.file_plating if df.file_plating is not None else True)
            # ferramenta marcada como via no arquivo (KiCad/Altium X2: "ViaDrill") ou Ø <= limite
            is_via = plated and (getattr(h, "via", False) or h.diameter <= via_max + 0.005)
            holes.append(Hole(h.x, h.y, h.diameter, plated, is_via, h.slot))
    return holes


def _smd_from_mask(mask: GerberLayer, holes: list[Hole]) -> int:
    """Estimativa de pads SMD pela máscara: aberturas que não coincidem com furos."""
    centers = mask.pad_centers()
    if not len(centers):
        return 0
    if not holes:
        return len(centers)
    hp = np.array([(h.x, h.y) for h in holes])
    hr = np.array([h.diameter / 2 + 0.05 for h in holes])
    count = 0
    for c in centers:
        d = np.hypot(hp[:, 0] - c[0], hp[:, 1] - c[1])
        if not np.any(d <= hr):
            count += 1
    return count


def evaluate(board: BoardData, options: AnalysisOptions | None = None) -> AnalysisResult:
    opt = options or AnalysisOptions()
    warnings = list(board.warnings)

    holes = _resolve_holes(board, opt.via_max_diameter)
    board.holes = holes
    pth_total = sum(1 for h in holes if h.plated)
    pth_component = sum(1 for h in holes if h.plated and not h.is_via)
    via_count = pth_total - pth_component
    npth_count = len(holes) - pth_total

    def smd_count(paste, mask, side):
        # Regra: componentes SMD são identificados pelos pads da camada Paste.
        if paste is not None:
            return paste.pad_count, "Paste"
        hint = _smd_from_mask(mask, holes) if mask is not None else 0
        if hint:  # sem Paste e sem indício na máscara: lado provavelmente sem SMD
            warnings.append(
                f"Camada Paste {side} ausente: SMD {side} não contabilizado. A máscara de solda sugere "
                f"~{hint} aberturas fora de furos (possíveis pads SMD): solicite a Paste ao cliente "
                f"ou reclassifique a máscara como Paste {side}.")
        return 0, "Paste ausente"

    smd_top, src_top = smd_count(board.paste_top, board.mask_top, "TOP")
    smd_bot, src_bot = smd_count(board.paste_bot, board.mask_bot, "BOT")

    top_pth = pth_component if opt.pth_top else 0
    bot_pth = pth_component if opt.pth_bot else 0
    if pth_component and not (opt.pth_top or opt.pth_bot):
        warnings.append(f"{pth_component} furos PTH de componentes detectados, mas nenhum lado "
                        "de inserção PTH foi marcado.")

    top = SideStatus("TOP", smd_top, top_pth, side_message("TOP", smd_top, top_pth))
    bot = SideStatus("BOT", smd_bot, bot_pth, side_message("BOT", smd_bot, bot_pth))

    height_auto = estimate_height(top, bot)
    height = opt.height_override if opt.height_override is not None else height_auto
    layers_auto = board.copper_layer_count
    layers = opt.layers_override if opt.layers_override is not None else layers_auto
    if layers_auto == 0:
        warnings.append("Nenhuma camada de cobre identificada: informe o número de layers.")

    # Agrupamento por broca, ordenado por diâmetro (como a Drill Table do Altium)
    groups: dict[tuple[float, str, bool], int] = {}
    for h in holes:
        key = (round(h.diameter, 3), h.kind, h.slot)
        groups[key] = groups.get(key, 0) + 1
    order = {"VIA": 0, "PTH": 1, "NPTH": 2}
    drill_groups = [DrillGroup(d, k, n, sl) for (d, k, sl), n in
                    sorted(groups.items(), key=lambda it: (it[0][0], order[it[0][1]], it[0][2]))]

    bx, by = board.width, board.height
    comps = count_components(board, opt.pth_top, opt.pth_bot)
    warnings += comps.warnings
    times = estimate_times(comps, bx, by, opt.time_params or TimeParams(), opt.lot_qty,
                           {"TOP": opt.smd_top_override, "BOT": opt.smd_bot_override},
                           opt.pth_override)
    return AnalysisResult(
        layers=layers, layers_auto=layers_auto,
        board_x=bx, board_y=by, area_cm2=bx * by / 100.0,
        smd_top=smd_top, smd_bot=smd_bot, smd_top_source=src_top, smd_bot_source=src_bot,
        pth_total=pth_total, pth_component=pth_component, via_count=via_count,
        npth_count=npth_count, drill_groups=drill_groups, top=top, bot=bot,
        assembly_height_auto=height_auto, assembly_height=height,
        package=package_dimensions(bx, by, height), warnings=warnings,
        shape=board.shape.shape if board.shape else "Indefinido",
        real_area_cm2=(board.shape.real_area_mm2 if board.shape else bx * by) / 100.0,
        components=comps, times=times,
    )


# --------------------------------------------------------------------------- #
# Atalho de alto nível (CLI / testes)
# --------------------------------------------------------------------------- #
@dataclass
class ProjectSession:
    project: ExtractedProject
    classified: list[ClassifiedFile]
    analyzer: ProjectAnalyzer
    board: BoardData
    result: AnalysisResult

    def reevaluate(self, options: AnalysisOptions) -> AnalysisResult:
        self.result = evaluate(self.board, options)
        return self.result

    def reload(self, options: AnalysisOptions | None = None) -> AnalysisResult:
        self.board = self.analyzer.load(self.classified)
        return self.reevaluate(options or AnalysisOptions())


def analyze_project(source: str | Path, options: AnalysisOptions | None = None) -> ProjectSession:
    project = extract_project(source)
    classified = classify_files(project.files)
    analyzer = ProjectAnalyzer()
    board = analyzer.load(classified)
    result = evaluate(board, options)
    return ProjectSession(project, classified, analyzer, board, result)
