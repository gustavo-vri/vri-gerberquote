"""Parsers leves e sem dependências externas para Gerber RS-274X/X2 e Excellon.

Todas as coordenadas e dimensões são convertidas para milímetros.

Suporte Gerber: FS, MO, AD (C, R, O, P e macros AM com expressões), LP, G01/G02/
G03, G74/G75, G36/G37 (regiões), D01/D02/D03, G54, G70/G71, atributos TF.
Suporte Excellon: METRIC/INCH, LZ/TZ, ``;FILE_FORMAT`` (Altium), ferramentas com
parâmetros F/S/C, coordenadas implícitas ou decimais, slots G85 e rotas G00
(contados como um furo), plating por arquivo ou por ferramenta (KiCad/Altium).
"""
from __future__ import annotations

import ast
import math
import operator
import re
from dataclasses import dataclass, field

import numpy as np

CIRCLE_SEGMENTS = 24
INCH = 25.4


# =========================================================================== #
# Utilidades geométricas
# =========================================================================== #
def circle_poly(cx: float, cy: float, r: float, n: int = CIRCLE_SEGMENTS) -> np.ndarray:
    t = np.linspace(0.0, 2.0 * math.pi, n, endpoint=False)
    return np.column_stack((cx + r * np.cos(t), cy + r * np.sin(t)))


def rect_poly(cx: float, cy: float, w: float, h: float) -> np.ndarray:
    return np.array([[cx - w / 2, cy - h / 2], [cx + w / 2, cy - h / 2],
                     [cx + w / 2, cy + h / 2], [cx - w / 2, cy + h / 2]])


def obround_poly(w: float, h: float, n: int = 13) -> np.ndarray:
    if abs(w - h) < 1e-9:
        return circle_poly(0, 0, w / 2, CIRCLE_SEGMENTS)
    if w > h:
        r, d = h / 2, (w - h) / 2
        a1 = np.linspace(-math.pi / 2, math.pi / 2, n)
        a2 = np.linspace(math.pi / 2, 3 * math.pi / 2, n)
        right = np.column_stack((d + r * np.cos(a1), r * np.sin(a1)))
        left = np.column_stack((-d + r * np.cos(a2), r * np.sin(a2)))
    else:
        r, d = w / 2, (h - w) / 2
        a1 = np.linspace(0, math.pi, n)
        a2 = np.linspace(math.pi, 2 * math.pi, n)
        right = np.column_stack((r * np.cos(a1), d + r * np.sin(a1)))
        left = np.column_stack((r * np.cos(a2), -d + r * np.sin(a2)))
    return np.vstack((right, left))


def rotate(points: np.ndarray, deg: float) -> np.ndarray:
    if not deg:
        return points
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return points @ np.array([[c, s], [-s, c]])


def arc_points(x0, y0, x1, y1, cx, cy, clockwise: bool, max_step_deg: float = 10.0):
    """Pontos (excluindo o inicial) de um arco de (x0,y0) a (x1,y1) com centro (cx,cy)."""
    r = math.hypot(x0 - cx, y0 - cy)
    a0 = math.atan2(y0 - cy, x0 - cx)
    a1 = math.atan2(y1 - cy, x1 - cx)
    if clockwise:
        sweep = a0 - a1
        if sweep <= 1e-9:
            sweep += 2 * math.pi
        sweep = -sweep
    else:
        sweep = a1 - a0
        if sweep <= 1e-9:
            sweep += 2 * math.pi
    steps = max(2, int(abs(math.degrees(sweep)) / max_step_deg) + 1)
    pts = []
    for k in range(1, steps + 1):
        a = a0 + sweep * k / steps
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    pts[-1] = (x1, y1)
    return pts


# =========================================================================== #
# Avaliador seguro de expressões de macros
# =========================================================================== #
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.USub: operator.neg, ast.UAdd: operator.pos}


def _eval_node(node):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval_node(node.left), _eval_node(node.right)
        if isinstance(node.op, ast.Div) and right == 0:
            return 0.0
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError("expressão inválida")


def eval_expr(expr: str, variables: dict[int, float]) -> float:
    e = expr.strip().replace("x", "*").replace("X", "*")
    e = re.sub(r"\$(\d+)", lambda m: f"({variables.get(int(m.group(1)), 0.0)!r})", e)
    if not e:
        return 0.0
    try:
        return _eval_node(ast.parse(e, mode="eval"))
    except (SyntaxError, ValueError, TypeError):
        return 0.0


# =========================================================================== #
# Gerber
# =========================================================================== #
@dataclass
class Aperture:
    code: int
    shape: str                      # "C", "R", "O", "P" ou nome da macro
    params: list[float]             # em mm (macros: parâmetros brutos)
    polys: list[np.ndarray] = field(default_factory=list)  # geometria local em mm

    @property
    def width(self) -> float:
        """Largura efetiva para traçados (D01)."""
        if self.shape in ("C", "P") and self.params:
            return self.params[0]
        if self.shape in ("R", "O") and len(self.params) >= 2:
            return min(self.params[0], self.params[1])
        if self.polys:
            xs = np.concatenate([p[:, 0] for p in self.polys])
            ys = np.concatenate([p[:, 1] for p in self.polys])
            return float(min(xs.max() - xs.min(), ys.max() - ys.min()))
        return 0.1

    def extent(self) -> float:
        """Raio máximo da abertura (mm)."""
        if not self.polys:
            return 0.0
        return float(max(np.abs(p).max() for p in self.polys))

    def half_size(self) -> tuple[float, float]:
        """Meia-largura e meia-altura do envelope da abertura (mm)."""
        if not self.polys:
            return (0.0, 0.0)
        return (float(max(np.abs(p[:, 0]).max() for p in self.polys)),
                float(max(np.abs(p[:, 1]).max() for p in self.polys)))


@dataclass
class Flash:
    x: float
    y: float
    aperture: Aperture


@dataclass
class Track:
    x1: float
    y1: float
    x2: float
    y2: float
    width: float


@dataclass
class GerberLayer:
    name: str = ""
    flashes: list[Flash] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)
    regions: list[np.ndarray] = field(default_factory=list)
    file_function: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.flashes or self.tracks or self.regions)

    @property
    def pad_count(self) -> int:
        """Flashes + regiões (alguns CADs exportam pads como regiões G36/G37)."""
        return len(self.flashes) + len(self.regions)

    def pad_centers(self) -> np.ndarray:
        pts = [(f.x, f.y) for f in self.flashes]
        pts += [tuple(r.mean(axis=0)) for r in self.regions]
        return np.array(pts, dtype=float).reshape(-1, 2)

    def bbox(self, centerline: bool = False):
        """(xmin, ymin, xmax, ymax) ou ``None``.

        ``centerline=True`` ignora a largura das aberturas (usado no contorno,
        cuja borda real é a linha central do traço).
        """
        xs: list[float] = []
        ys: list[float] = []
        for t in self.tracks:
            h = 0.0 if centerline else t.width / 2
            xs += [min(t.x1, t.x2) - h, max(t.x1, t.x2) + h]
            ys += [min(t.y1, t.y2) - h, max(t.y1, t.y2) + h]
        for f in self.flashes:
            hx, hy = (0.0, 0.0) if centerline else f.aperture.half_size()
            xs += [f.x - hx, f.x + hx]
            ys += [f.y - hy, f.y + hy]
        for r in self.regions:
            xs += [float(r[:, 0].min()), float(r[:, 0].max())]
            ys += [float(r[:, 1].min()), float(r[:, 1].max())]
        if not xs:
            return None
        return (min(xs), min(ys), max(xs), max(ys))


_RE_FS = re.compile(r"FS([LTD]?)([AI]?).*?X(\d)(\d)Y(\d)(\d)")
_RE_AD = re.compile(r"ADD(\d+)([^,]+)(?:,(.*))?", re.S)
_RE_WORD_COORD = re.compile(r"([XYIJ])([+-]?[\d.]+)")
_RE_TF = re.compile(r"TF\.FileFunction,(.*)")


def _macro_polys(body: list[str], args: list[float]) -> list[np.ndarray]:
    """Converte uma macro AM em polígonos (unidades do arquivo)."""
    variables = {i + 1: v for i, v in enumerate(args)}
    polys: list[np.ndarray] = []
    for stmt in body:
        stmt = stmt.strip()
        if not stmt or (stmt[0] == "0" and (len(stmt) == 1 or stmt[1] in " ,")):
            continue  # vazio ou primitiva 0 (comentário)
        m = re.match(r"^\$(\d+)\s*=\s*(.+)$", stmt)
        if m:
            variables[int(m.group(1))] = eval_expr(m.group(2), variables)
            continue
        parts = [p for p in stmt.split(",")]
        try:
            code = int(eval_expr(parts[0], variables))
        except ValueError:
            continue
        v = [eval_expr(p, variables) for p in parts[1:]]

        def g(i, default=0.0):
            return v[i] if i < len(v) else default

        if g(0, 1.0) == 0 and code in (1, 2, 20, 21, 22, 4, 5):
            continue  # exposição "clear": ignorada na aproximação
        if code == 1:
            polys.append(rotate(circle_poly(g(2), g(3), g(1) / 2), g(4)))
        elif code in (2, 20):
            w, x1, y1, x2, y2, rot = g(1), g(2), g(3), g(4), g(5), g(6)
            ang = math.atan2(y2 - y1, x2 - x1)
            nx, ny = -math.sin(ang) * w / 2, math.cos(ang) * w / 2
            p = np.array([[x1 + nx, y1 + ny], [x2 + nx, y2 + ny], [x2 - nx, y2 - ny], [x1 - nx, y1 - ny]])
            polys.append(rotate(p, rot))
        elif code == 21:
            polys.append(rotate(rect_poly(g(3), g(4), g(1), g(2)), g(5)))
        elif code == 22:
            w, h = g(1), g(2)
            polys.append(rotate(rect_poly(g(3) + w / 2, g(4) + h / 2, w, h), g(5)))
        elif code == 4:
            n = int(g(1))
            pts = [(g(2 + 2 * k), g(3 + 2 * k)) for k in range(n + 1)]
            if len(pts) >= 3:
                polys.append(rotate(np.array(pts, dtype=float), g(2 + 2 * (n + 1))))
        elif code == 5:
            n = max(3, int(g(1)))
            t = np.linspace(0, 2 * math.pi, n, endpoint=False)
            p = np.column_stack((g(2) + g(4) / 2 * np.cos(t), g(3) + g(4) / 2 * np.sin(t)))
            polys.append(rotate(p, g(5)))
        elif code in (6, 7):
            polys.append(rotate(circle_poly(g(0), g(1), g(2) / 2), g(len(v) - 1) if code == 7 else 0))
    return polys


class _GerberState:
    def __init__(self):
        self.scale = INCH  # padrão histórico até ler MO
        self.units_set = False
        self.zero_mode = "L"
        self.incremental = False
        self.xi, self.xd, self.yi, self.yd = 2, 4, 2, 4
        self.x = self.y = 0.0
        self.interp = 1
        self.multi_quadrant = True
        self.region = False
        self.contour: list[tuple[float, float]] = []
        self.aperture: Aperture | None = None
        self.last_d = 2
        self.dark = True

    def num(self, s: str, axis: str) -> float:
        if "." in s:
            return float(s) * self.scale
        neg = s.startswith("-")
        digits = s.lstrip("+-")
        ip, dp = (self.xi, self.xd) if axis in "XI" else (self.yi, self.yd)
        if self.zero_mode == "T":
            digits = digits.ljust(ip + dp, "0")
        val = int(digits or "0") / (10 ** dp)
        return (-val if neg else val) * self.scale


def parse_gerber(text: str, name: str = "") -> GerberLayer:
    layer = GerberLayer(name=name)
    st = _GerberState()
    macros: dict[str, list[str]] = {}
    apertures: dict[int, Aperture] = {}

    def close_contour():
        if len(st.contour) >= 3 and st.dark:
            layer.regions.append(np.array(st.contour, dtype=float))
        st.contour = []

    def handle_ext(stmt: str):
        stmt = stmt.strip()
        if not stmt:
            return
        if stmt.startswith("FS"):
            m = _RE_FS.search(stmt)
            if m:
                st.zero_mode = "T" if m.group(1) == "T" else "L"
                st.incremental = m.group(2) == "I"
                st.xi, st.xd, st.yi, st.yd = (int(m.group(k)) for k in range(3, 7))
        elif stmt.startswith("MO"):
            st.scale = INCH if "IN" in stmt[2:4] else 1.0
            st.units_set = True
        elif stmt.startswith("AD"):
            m = _RE_AD.match(stmt)
            if not m:
                return
            code, shape = int(m.group(1)), m.group(2).strip()
            raw = [p for p in (m.group(3) or "").split("X") if p.strip() != ""]
            if shape in ("C", "R", "O", "P"):
                try:
                    vals = [float(p) for p in raw]
                except ValueError:
                    vals = []
                if shape == "P":
                    mm = [vals[0] * st.scale] + vals[1:] if vals else [0.0]
                else:
                    mm = [v * st.scale for v in vals]
                ap = Aperture(code, shape, mm)
                if shape == "C":
                    ap.polys = [circle_poly(0, 0, (mm[0] if mm else 0.1) / 2)]
                elif shape == "R" and len(mm) >= 2:
                    ap.polys = [rect_poly(0, 0, mm[0], mm[1])]
                elif shape == "O" and len(mm) >= 2:
                    ap.polys = [obround_poly(mm[0], mm[1])]
                elif shape == "P" and mm:
                    n = int(mm[1]) if len(mm) > 1 else 6
                    t = np.linspace(0, 2 * math.pi, max(3, n), endpoint=False)
                    p = np.column_stack((mm[0] / 2 * np.cos(t), mm[0] / 2 * np.sin(t)))
                    ap.polys = [rotate(p, mm[2] if len(mm) > 2 else 0)]
            else:
                try:
                    args = [float(p) for p in raw]
                except ValueError:
                    args = []
                body = macros.get(shape, [])
                polys = [p * st.scale for p in _macro_polys(body, args)]
                ap = Aperture(code, shape, args, polys or [circle_poly(0, 0, 0.05)])
                if not body:
                    layer.warnings.append(f"Macro '{shape}' não definida (aproximada).")
            apertures[code] = ap
        elif stmt.startswith("LP"):
            st.dark = not stmt.startswith("LPC")
        elif stmt.startswith("TF"):
            m = _RE_TF.search(stmt)
            if m:
                layer.file_function = m.group(1)
        elif stmt.startswith("SR") and stmt not in ("SR", "SRX1Y1I0J0"):
            if "X1Y1" not in stmt:
                layer.warnings.append("Step & Repeat (SR) não expandido.")

    def handle_word(word: str):
        if not word:
            return
        if word.startswith("G04") or word.startswith("G4 ") or word == "G4":
            return
        if word.startswith(("M02", "M00", "M2")):
            return
        for g in re.findall(r"G(\d+)", word):
            g = int(g)
            if g in (1, 2, 3):
                st.interp = g
            elif g == 36:
                st.region, st.contour = True, []
            elif g == 37:
                close_contour()
                st.region = False
            elif g == 74:
                st.multi_quadrant = False
            elif g == 75:
                st.multi_quadrant = True
            elif g == 70:
                st.scale = INCH
            elif g == 71:
                st.scale = 1.0
            elif g == 91:
                st.incremental = True
            elif g == 90:
                st.incremental = False
        coords = {k: st.num(v, k) for k, v in _RE_WORD_COORD.findall(word)}
        dm = re.search(r"D(\d+)", word)
        d = int(dm.group(1)) if dm else None
        if d is not None and d >= 10:
            st.aperture = apertures.get(d)
            return
        if d is None:
            if not coords:
                return
            d = st.last_d  # código D modal (formato antigo)
        st.last_d = d

        if st.incremental:
            nx = st.x + coords.get("X", 0.0)
            ny = st.y + coords.get("Y", 0.0)
        else:
            nx = coords.get("X", st.x)
            ny = coords.get("Y", st.y)

        if d == 1:
            if st.interp == 1:
                pts = [(nx, ny)]
            else:
                pts = _arc(st, nx, ny, coords.get("I", 0.0), coords.get("J", 0.0))
            if st.region:
                if not st.contour:
                    st.contour.append((st.x, st.y))
                st.contour.extend(pts)
            elif st.dark:
                w = st.aperture.width if st.aperture else 0.1
                px, py = st.x, st.y
                for qx, qy in pts:
                    layer.tracks.append(Track(px, py, qx, qy, w))
                    px, py = qx, qy
        elif d == 2:
            if st.region:
                close_contour()
        elif d == 3:
            if st.aperture is not None and st.dark and not st.region:
                layer.flashes.append(Flash(nx, ny, st.aperture))
        st.x, st.y = nx, ny

    # --- tokenização --------------------------------------------------------
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "%":
            j = text.find("%", i + 1)
            j = n if j < 0 else j
            block = text[i + 1:j].replace("\r", "").replace("\n", "")
            if block.startswith("AM"):
                stmts = block.split("*")
                macros[stmts[0][2:].strip()] = stmts[1:]
            else:
                for stmt in block.split("*"):
                    handle_ext(stmt)
            i = j + 1
        elif c in " \r\n\t":
            i += 1
        else:
            j = text.find("*", i)
            if j < 0:
                break
            handle_word(text[i:j].strip())
            i = j + 1
    if st.contour:
        close_contour()
    return layer


def _arc(st: _GerberState, nx: float, ny: float, i: float, j: float):
    cw = st.interp == 2
    x0, y0 = st.x, st.y
    if st.multi_quadrant:
        cx, cy = x0 + i, y0 + j
    else:  # G74: I/J sem sinal; escolhe o centro mais consistente (arco <= 90°)
        best = None
        for sx in (1, -1):
            for sy in (1, -1):
                cx, cy = x0 + sx * abs(i), y0 + sy * abs(j)
                err = abs(math.hypot(x0 - cx, y0 - cy) - math.hypot(nx - cx, ny - cy))
                a0 = math.atan2(y0 - cy, x0 - cx)
                a1 = math.atan2(ny - cy, nx - cx)
                sweep = (a0 - a1) if cw else (a1 - a0)
                sweep %= 2 * math.pi
                if sweep <= math.pi / 2 + 1e-6 and (best is None or err < best[0]):
                    best = (err, cx, cy)
        cx, cy = (best[1], best[2]) if best else (x0 + i, y0 + j)
        if abs(x0 - nx) < 1e-9 and abs(y0 - ny) < 1e-9:
            return [(nx, ny)]
    return arc_points(x0, y0, nx, ny, cx, cy, cw)


# =========================================================================== #
# Excellon
# =========================================================================== #
@dataclass
class DrillTool:
    number: int
    diameter: float              # mm
    plated: bool | None = None   # None = não informado na ferramenta
    via: bool = False            # marcada como furo de via (atributo X2 "ViaDrill")


@dataclass
class DrillHole:
    x: float
    y: float
    diameter: float
    tool: int
    plated: bool | None = None
    slot: bool = False
    via: bool = False


@dataclass
class DrillFile:
    name: str = ""
    tools: dict[int, DrillTool] = field(default_factory=dict)
    holes: list[DrillHole] = field(default_factory=list)
    file_plating: bool | None = None
    warnings: list[str] = field(default_factory=list)


_RE_TOOL_DEF = re.compile(r"^T(\d+)((?:[FSBHZ][+-]?[\d.]+)*)C([\d.]+)")
_RE_TOOL_SEL = re.compile(r"^T(\d+)$")
_RE_EXC_XY = re.compile(r"([XY])([+-]?[\d.]+)")


def parse_excellon(text: str, name: str = "") -> DrillFile:
    df = DrillFile(name=name)
    scale = INCH
    int_d, dec_d = 2, 4
    zeros = None           # "LZ" | "TZ" | None
    explicit_format = False
    current: DrillTool | None = None
    pending_plating: bool | None = None
    section_plating: bool | None = None
    pending_via = False
    kinds_seen: set[bool] = set()
    x = y = 0.0
    route_mode = False

    def conv(s: str) -> float:
        if "." in s:
            return float(s) * scale
        neg = s.startswith("-")
        digits = s.lstrip("+-")
        if zeros == "LZ" or (zeros is None and explicit_format):
            digits = digits.ljust(int_d + dec_d, "0")  # zeros finais suprimidos
        val = int(digits or "0") / (10 ** dec_d)
        return (-val if neg else val) * scale

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        up = line.upper()
        if up.startswith(";"):
            # Altium: ";TYPE=PLATED" / ";TYPE=NON_PLATED" valem para as ferramentas seguintes
            if re.search(r"TYPE\s*=\s*NON[_\-]?PLATED", up) or "FILEFUNCTION,NONPLATED" in up:
                section_plating = False
                kinds_seen.add(False)
            elif re.search(r"TYPE\s*=\s*PLATED", up) or "FILEFUNCTION,PLATED" in up:
                section_plating = True
                kinds_seen.add(True)
            if "TA.APERFUNCTION,NONPLATED" in up:
                pending_plating = False
            elif "TA.APERFUNCTION,PLATED" in up:
                pending_plating = True
                pending_via = "VIADRILL" in up
            m = re.search(r"FILE_FORMAT\s*=\s*(\d+)\s*:\s*(\d+)", up)
            if m:
                int_d, dec_d = int(m.group(1)), int(m.group(2))
                explicit_format = True
            continue
        if up.startswith(("METRIC", "INCH")) or up in ("M71", "M72"):
            is_metric = up.startswith("METRIC") or up == "M71"
            scale = 1.0 if is_metric else INCH
            if not explicit_format:
                int_d, dec_d = (3, 3) if is_metric else (2, 4)
            if ",LZ" in up:
                zeros = "LZ"
            elif ",TZ" in up:
                zeros = "TZ"
            m = re.search(r",(0+)\.(0+)", up)
            if m:
                int_d, dec_d = len(m.group(1)), len(m.group(2))
                explicit_format = True
            continue
        m = _RE_TOOL_DEF.match(up)
        if m:
            num = int(m.group(1))
            plating = pending_plating if pending_plating is not None else section_plating
            tool = DrillTool(num, float(m.group(3)) * scale, plating, pending_via)
            df.tools[num] = tool
            pending_plating = None
            pending_via = False
            if "X" in up or "Y" in up:  # ferramenta definida no corpo
                current = tool
            continue
        m = _RE_TOOL_SEL.match(up)
        if m:
            num = int(m.group(1))
            current = df.tools.get(num) if num else None
            if num and current is None:
                df.warnings.append(f"Ferramenta T{num} sem diâmetro definido.")
            continue
        if up.startswith(("G93", "M48", "%", "M30", "M95")):
            continue  # zero de máquina / delimitadores
        if up.startswith("G00"):
            route_mode = True
        elif up.startswith("G05") or up.startswith("M17"):
            route_mode = False
        if up.startswith(("M15", "M16", "G01", "G02", "G03")) or (route_mode and not up.startswith("G00")):
            continue  # movimentos de rota: o furo/slot já foi contado no G00
        coords = _RE_EXC_XY.findall(up.split("G85")[0])
        if not coords:
            continue
        for k, v in coords:
            if k == "X":
                x = conv(v)
            else:
                y = conv(v)
        if current is None:
            continue
        df.holes.append(DrillHole(x, y, current.diameter, current.number,
                                  current.plated, slot="G85" in up or up.startswith("G00"),
                                  via=current.via))
    if len(kinds_seen) == 1:
        df.file_plating = next(iter(kinds_seen))
    return df
