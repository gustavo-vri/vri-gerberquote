"""Gerador de projetos Gerber/Excellon sintéticos no padrão de saída do Altium.

Usado pela suíte de testes e útil para demonstrações (``python -m tests.synthetic``).
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

INCH = 25.4


@dataclass
class GerberWriter:
    """Gera um Gerber RS-274X estilo Altium (polegadas 2:5 ou mm 4:4)."""

    layer_name: str
    units: str = "in"
    lines: list[str] = field(default_factory=list)
    apertures: dict[tuple, int] = field(default_factory=dict)
    _next_ap: int = 10
    _cur_ap: int | None = None

    def _n(self, mm: float) -> str:
        if self.units == "in":
            return str(int(round(mm / INCH * 1e5)))
        return str(int(round(mm * 1e4)))

    def _size(self, mm: float) -> str:
        return f"{mm / INCH:.5f}" if self.units == "in" else f"{mm:.4f}"

    def _ap(self, shape: str, *dims: float) -> int:
        key = (shape, *dims)
        if key not in self.apertures:
            self.apertures[key] = self._next_ap
            self._next_ap += 1
        code = self.apertures[key]
        if self._cur_ap != code:
            self.lines.append(f"G54D{code}*")
            self._cur_ap = code
        return code

    def flash_rect(self, x: float, y: float, w: float, h: float) -> None:
        self._ap("R", w, h)
        self.lines.append(f"X{self._n(x)}Y{self._n(y)}D03*")

    def flash_circle(self, x: float, y: float, d: float) -> None:
        self._ap("C", d)
        self.lines.append(f"X{self._n(x)}Y{self._n(y)}D03*")

    def line(self, x1, y1, x2, y2, w=0.2) -> None:
        self._ap("C", w)
        self.lines.append(f"X{self._n(x1)}Y{self._n(y1)}D02*")
        self.lines.append(f"X{self._n(x2)}Y{self._n(y2)}D01*")

    def rect_outline(self, w: float, h: float, lw: float = 0.1, corner: float = 0.0) -> None:
        self._ap("C", lw)
        if corner <= 0:
            pts = [(0, 0), (w, 0), (w, h), (0, h), (0, 0)]
            self.lines.append(f"X{self._n(0)}Y{self._n(0)}D02*")
            for x, y in pts[1:]:
                self.lines.append(f"X{self._n(x)}Y{self._n(y)}D01*")
            return
        r = corner  # cantos arredondados com G03 (anti-horário), modo G75
        n = self._n
        self.lines += [
            f"X{n(r)}Y{n(0)}D02*",
            f"G01X{n(w - r)}Y{n(0)}D01*",
            f"G03X{n(w)}Y{n(r)}I{n(0)}J{n(r)}D01*",
            f"G01X{n(w)}Y{n(h - r)}D01*",
            f"G03X{n(w - r)}Y{n(h)}I{n(-r)}J{n(0)}D01*",
            f"G01X{n(r)}Y{n(h)}D01*",
            f"G03X{n(0)}Y{n(h - r)}I{n(0)}J{n(-r)}D01*",
            f"G01X{n(0)}Y{n(r)}D01*",
            f"G03X{n(r)}Y{n(0)}I{n(r)}J{n(0)}D01*",
            "G01*",
        ]

    def polygon_outline(self, pts: list[tuple[float, float]], lw: float = 0.1) -> None:
        self._ap("C", lw)
        n = self._n
        self.lines.append(f"X{n(pts[0][0])}Y{n(pts[0][1])}D02*")
        for x, y in pts[1:] + [pts[0]]:
            self.lines.append(f"X{n(x)}Y{n(y)}D01*")

    def region_rect(self, x: float, y: float, w: float, h: float) -> None:
        n = self._n
        self.lines += ["G36*", f"X{n(x)}Y{n(y)}D02*", f"X{n(x + w)}Y{n(y)}D01*",
                       f"X{n(x + w)}Y{n(y + h)}D01*", f"X{n(x)}Y{n(y + h)}D01*",
                       f"X{n(x)}Y{n(y)}D01*", "G37*"]

    def render(self) -> str:
        if self.units == "in":
            head = ["G04 Layer: " + self.layer_name + "*", "G04 Altium Designer (sintético)*",
                    "%FSLAX25Y25*%", "%MOIN*%", "G70*", "G01*", "G75*"]
        else:
            head = ["G04 Layer: " + self.layer_name + "*", "G04 Altium Designer (sintético)*",
                    "%FSLAX44Y44*%", "%MOMM*%", "G71*", "G01*", "G75*"]
        ads = []
        for key, code in self.apertures.items():
            shape, *dims = key
            ads.append(f"%ADD{code}{shape},{'X'.join(self._size(d) for d in dims)}*%")
        return "\n".join(head + ads + self.lines + ["M02*", ""])


def excellon_altium(holes: list[tuple[float, float, float]], plated: bool | None,
                    units: str = "in") -> str:
    """Excellon estilo Altium (``;FILE_FORMAT=2:5`` + ``INCH,LZ`` ou mm 4:4)."""
    tools: dict[float, int] = {}
    for _, _, d in holes:
        tools.setdefault(round(d, 4), len(tools) + 1)
    out = ["M48", ";Layer_Color=9474304"]
    if units == "in":
        out += [";FILE_FORMAT=2:5", "INCH,LZ"]
    else:
        out += [";FILE_FORMAT=4:4", "METRIC,LZ"]
    if plated is True:
        out.append(";TYPE=PLATED")
    elif plated is False:
        out.append(";TYPE=NON_PLATED")
    for d, t in tools.items():
        size = f"{d / INCH:.5f}" if units == "in" else f"{d:.3f}"
        out.append(f"T{t}F00S00C{size}")
    out += ["%", "G90", "G05"]

    def coord(mm: float) -> str:
        if units == "in":
            v = int(round(mm / INCH * 1e5))
            s = f"{abs(v):07d}".rstrip("0") or "0"
        else:
            v = int(round(mm * 1e4))
            s = f"{abs(v):08d}".rstrip("0") or "0"
        return ("-" if v < 0 else "") + s

    for d, t in tools.items():
        out.append(f"T{t:02d}")
        for x, y, hd in holes:
            if round(hd, 4) == d:
                out.append(f"X{coord(x)}Y{coord(y)}")
    out += ["M30", ""]
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Cenários
# --------------------------------------------------------------------------- #
def _smd_grid(g: GerberWriter, x0, y0, nx, ny, pitch=2.0, w=1.0, h=0.6):
    for i in range(nx):
        for j in range(ny):
            g.flash_rect(x0 + i * pitch, y0 + j * pitch, w, h)


def _noise_files(z: zipfile.ZipFile, prefix: str = ""):
    z.writestr(prefix + "Status Report.Txt", "Altium Designer Output Status\nOutput Name: Gerber Files\n")
    z.writestr(prefix + "Proj.BOM.txt", "Bill of materials\nItem Qty Description\n1 2 10k\n")
    z.writestr(prefix + "Proj.IPC", "P  JOB Proj\n327VCC R1 -1 D0300PA00X+001000Y+001000\n")
    z.writestr(prefix + "Fab Notes.pdf", b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\n")
    z.writestr(prefix + "Proj.DRR", "Drill report\nT1 1.0mm 8 holes\n")


def scenario_4l_smdtop_pth(path: Path) -> Path:
    """4 layers, pastas /Gerber e /Drill, SMD Top + PTH, lado Bot sem componentes."""
    W, H = 100.0, 80.0
    files: dict[str, str] = {}
    top = GerberWriter("TopLayer"); bot = GerberWriter("BottomLayer")
    in1 = GerberWriter("MidLayer1"); in2 = GerberWriter("MidLayer2")
    gtp = GerberWriter("TopPaste"); gbp = GerberWriter("BottomPaste")
    gto = GerberWriter("TopOverlay"); gko = GerberWriter("KeepOutLayer")
    gko.rect_outline(W, H)
    _smd_grid(gtp, 10, 10, 6, 4)            # 24 pads SMD top
    gtp.region_rect(60, 10, 5, 5)           # +1 pad térmico como região = 25
    _smd_grid(top, 10, 10, 6, 4)
    top.line(10, 10, 60, 60, 0.25)
    bot.line(5, 5, 95, 5, 0.3)
    in1.region_rect(2, 2, W - 4, H - 4)
    in2.region_rect(2, 2, W - 4, H - 4)
    gto.line(8, 8, 25, 8, 0.15)
    pth = [(30 + i * 2.54, 50, 1.0) for i in range(8)]    # conector 8 pinos Ø1,0
    pth += [(70, 60, 1.2), (75, 60, 1.2)]                 # 2 bornes Ø1,2
    vias = [(40, 30, 0.3), (45, 30, 0.3), (50, 30, 0.3)]
    npth = [(4, 4, 3.2), (96, 4, 3.2), (4, 76, 3.2), (96, 76, 3.2)]
    for wtr, ext in [(top, "GTL"), (bot, "GBL"), (in1, "G1"), (in2, "G2"), (gtp, "GTP"),
                     (gbp, "GBP"), (gto, "GTO"), (gko, "GKO")]:
        files[f"Gerber/Proj.{ext}"] = wtr.render()
    files["Drill/Proj-Plated.TXT"] = excellon_altium(pth + vias, True)
    files["Drill/Proj-NonPlated.TXT"] = excellon_altium(npth, False)
    with zipfile.ZipFile(path, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
        _noise_files(z, "Gerber/")
        z.writestr("Drill/Proj-RoundHoles.TXT", "Round holes report\nHole size  count\n1.0mm 8\n")
    return path


def scenario_2l_smd_both(path: Path) -> Path:
    """2 layers, SMD Top e Bot, sem PTH (apenas furos mecânicos NPTH), em mm."""
    W, H = 60.0, 40.0
    top = GerberWriter("TopLayer", "mm"); bot = GerberWriter("BottomLayer", "mm")
    gtp = GerberWriter("TopPaste", "mm"); gbp = GerberWriter("BottomPaste", "mm")
    gto = GerberWriter("TopOverlay", "mm"); gbo = GerberWriter("BottomOverlay", "mm")
    gm1 = GerberWriter("Mechanical1", "mm")
    gm1.rect_outline(W, H, 0.1, corner=3.0)
    _smd_grid(gtp, 8, 8, 5, 3)      # 15 pads top
    _smd_grid(gbp, 30, 10, 4, 2)    # 8 pads bot
    _smd_grid(top, 8, 8, 5, 3)
    _smd_grid(bot, 30, 10, 4, 2)
    gto.line(5, 5, 20, 5); gbo.line(30, 5, 50, 5)
    npth = [(3.5, 3.5, 3.0), (56.5, 3.5, 3.0), (3.5, 36.5, 3.0), (56.5, 36.5, 3.0)]
    with zipfile.ZipFile(path, "w") as z:
        for wtr, ext in [(top, "GTL"), (bot, "GBL"), (gtp, "GTP"), (gbp, "GBP"),
                         (gto, "GTO"), (gbo, "GBO"), (gm1, "GM1")]:
            z.writestr(f"SMD_Board.{ext}", wtr.render())
        # arquivo único, tipo indicado só no cabeçalho
        z.writestr("SMD_Board.TXT", excellon_altium(npth, False, "mm"))
        _noise_files(z)
    return path


def scenario_1l_pth_only(path: Path) -> Path:
    """1 layer (cobre bottom), somente PTH, sem SMD."""
    W, H = 50.0, 30.0
    bot = GerberWriter("BottomLayer"); gto = GerberWriter("TopOverlay")
    gko = GerberWriter("KeepOutLayer")
    gko.rect_outline(W, H)
    pth = [(10 + i * 2.54, 15, 0.9) for i in range(6)] + [(40, 10, 1.3), (40, 20, 1.3)]
    for x, y, _ in pth:
        bot.flash_circle(x, y, 1.8)
    gto.line(8, 12, 25, 12)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("PTH_Only.GBL", bot.render())
        z.writestr("PTH_Only.GTO", gto.render())
        z.writestr("PTH_Only.GKO", gko.render())
        z.writestr("PTH_Only-Plated.TXT", excellon_altium(pth, True))
    return path


def scenario_l_shape(path: Path) -> Path:
    """Placa em L (120 x 90 mm com recorte de 50 x 40 mm), 2 layers, SMD Top, sem PTH."""
    gko, top, gtp = GerberWriter("KeepOutLayer"), GerberWriter("TopLayer"), GerberWriter("TopPaste")
    gko.polygon_outline([(0, 0), (120, 0), (120, 50), (70, 50), (70, 90), (0, 90)])
    _smd_grid(gtp, 10, 10, 4, 3)
    _smd_grid(top, 10, 10, 4, 3)
    bot = GerberWriter("BottomLayer")
    bot.line(5, 5, 60, 5)
    with zipfile.ZipFile(path, "w") as z:
        for wtr, ext in [(gko, "GKO"), (top, "GTL"), (gtp, "GTP"), (bot, "GBL")]:
            z.writestr(f"Placa_L.{ext}", wtr.render())
        z.writestr("Placa_L-NonPlated.TXT", excellon_altium([(5, 85, 3.2), (115, 5, 3.2)], False))
    return path


def _silk_shape(wr: "GerberWriter", item) -> None:
    import math as _m

    if item[0] == "circle":
        _, cx, cy, r = item
        wr.polygon_outline([(cx + r * _m.cos(a / 36 * 2 * _m.pi), cy + r * _m.sin(a / 36 * 2 * _m.pi))
                            for a in range(36)], 0.15)
    elif item[0] == "oval":
        _, x0, y0, x1, y1 = item
        r = (y1 - y0) / 2
        pts = [(x1 - r + r * _m.cos(a), y0 + r + r * _m.sin(a)) for a in [(-90 + k * 15) * _m.pi / 180 for k in range(13)]]
        pts += [(x0 + r + r * _m.cos(a), y0 + r + r * _m.sin(a)) for a in [(90 + k * 15) * _m.pi / 180 for k in range(13)]]
        wr.polygon_outline(pts, 0.15)
    else:
        x, y, rw, rh = item
        wr.polygon_outline([(x, y), (x + rw, y), (x + rw, y + rh), (x, y + rh)], 0.15)


# --------------------------------------------------------------------------- #
# Cenário 5: placa mista realista com contagem de componentes conhecida
# --------------------------------------------------------------------------- #
MIXED_EXPECTED = {"smd_top": 15, "smd_bot": 3, "pth": 7}


def _mixed_geometry():
    """Retorna (pads_top, pads_bot, pth_holes, npth, silk_rects, placements)."""
    top, bot, pth, silk, pnp = [], [], [], [], []

    def comp(ref, fp, side, x, y, pads=(), holes=(), value=""):
        (top if side == "TOP" else bot).extend(pads)
        pth.extend(holes)
        pnp.append((ref, value, side, fp, x, y))

    # 6 resistores 0603 empilhados (pads 0,8 x 0,95, centros a ±0,8 mm)
    for i in range(6):
        x, y = 10.0, 10.0 + i * 2.0
        comp(f"R{i + 1}", "RES_0603", "TOP", x, y, [(x - 0.8, y, 0.8, 0.95), (x + 0.8, y, 0.8, 0.95)], value="10k")
    # 4 capacitores 0402 (pads 0,5 x 0,55)
    for i in range(4):
        x, y = 20.0, 10.0 + i * 1.2
        comp(f"C{i + 1}", "CAP_0402", "TOP", x, y, [(x - 0.48, y, 0.5, 0.55), (x + 0.48, y, 0.5, 0.55)],
             value="100nF")
    # SOIC-8
    x, y = 40.0, 15.0
    comp("U1", "SOIC-8", "TOP", x, y,
         [(x + (k - 1.5) * 1.27, y + s * 2.7, 0.6, 1.55) for s in (-1, 1) for k in range(4)], value="LM358")
    # QFP-32 (8 pinos por lado, passo 0,8)
    x, y = 60.0, 20.0
    q = [(x + (k - 3.5) * 0.8, y + s * 4.25, 0.45, 1.5) for s in (-1, 1) for k in range(8)]
    q += [(x + s * 4.25, y + (k - 3.5) * 0.8, 1.5, 0.45) for s in (-1, 1) for k in range(8)]
    comp("U2", "LQFP-32", "TOP", x, y, q, value="MCU")
    # SOT-23
    x, y = 30.0, 30.0
    comp("Q1", "SOT-23", "TOP", x, y, [(x - 0.95, y - 1.05, 0.6, 0.75), (x + 0.95, y - 1.05, 0.6, 0.75),
                                      (x, y + 1.05, 0.6, 0.75)], value="BC817")
    # QFN-16 4x4 passo 0,65 com pad térmico
    x, y = 80.0, 40.0
    qn = [(x + (k - 1.5) * 0.65, y + s * 1.95, 0.3, 0.8) for s in (-1, 1) for k in range(4)]
    qn += [(x + s * 1.95, y + (k - 1.5) * 0.65, 0.8, 0.3) for s in (-1, 1) for k in range(4)]
    qn.append((x, y, 2.1, 2.1))
    comp("U3", "QFN-16", "TOP", x, y, qn, value="DRV")
    # Capacitor eletrolítico SMD (pads 1,0 x 2,5 a ±2,2 mm)
    x, y = 45.0, 40.0
    comp("C10", "CAP_ELEC_6.3x5.4", "TOP", x, y, [(x - 2.2, y, 1.0, 2.5), (x + 2.2, y, 1.0, 2.5)], value="100uF")
    # 3 resistores 0805 no BOT
    for i in range(3):
        x, y = 70.0, 60.0 + i * 3.0
        comp(f"R{20 + i}", "RES_0805", "BOT", x, y, [(x - 0.95, y, 1.0, 1.3), (x + 0.95, y, 1.0, 1.3)],
             value="1k")
    # PTH ---------------------------------------------------------------
    x, y = 15.0, 45.0                                    # DIP-8
    comp("U4", "DIP-8", "TOP", x + 3.81, y + 3.81,
         holes=[(x + k * 2.54, y + s, 0.8) for s in (0.0, 7.62) for k in range(4)], value="NE555")
    comp("J1", "PinHeader_1x06", "TOP", 30 + 6.35, 55.0, holes=[(30 + k * 2.54, 55.0, 1.0) for k in range(6)])
    for i, yy in enumerate((62.0, 67.0)):              # 2 axiais paralelos (passo 10,16)
        comp(f"R{30 + i}", "AXIAL-0.4", "TOP", 15.08, yy, holes=[(10.0, yy, 0.8), (20.16, yy, 0.8)], value="1k/1W")
        silk.append((11.5, yy - 1.2, 7.16, 2.4))        # corpo do axial entre os furos
    comp("C20", "CP_Radial_D5.0mm", "TOP", 51.25, 62.0, holes=[(50.0, 62.0, 0.8), (52.5, 62.0, 0.8)], value="47uF")
    silk.append(("circle", 51.25, 62.0, 2.6))           # contorno circular do radial
    comp("J2", "TerminalBlock_3P_5.08", "TOP", 85.08, 70.0,
         holes=[(80.0 + k * 5.08, 70.0, 1.3) for k in range(3)])
    comp("Q2", "TO-220-3", "TOP", 62.54, 75.0, holes=[(60.0 + k * 2.54, 75.0, 1.0) for k in range(3)], value="IRFZ44")
    silk += [(28.0, 27.5, 4.0, 5.0), (76.6, 36.6, 6.8, 6.8)]   # contornos SOT-23 e QFN
    npth = [(3.0, 3.0, 3.2), (97.0, 3.0, 3.2)]
    pnp.append(("FID1", "", "TOP", "Fiducial_1mm", 5.0, 75.0))
    pnp.append(("MH1", "", "TOP", "MountingHole_3.2mm", 3.0, 3.0))
    return top, bot, pth, npth, silk, pnp


def altium_pick_place(pnp) -> str:
    out = ["Altium Designer Pick and Place Locations", "C:\\Proj\\Pick Place for Mista.txt", "",
           "=" * 80, "File Design Information:", "", "Units used: mm", "",
           '"Designator","Comment","Layer","Footprint","Center-X(mm)","Center-Y(mm)","Rotation","Description"']
    for ref, val, side, fp, x, y in pnp:
        layer = "TopLayer" if side == "TOP" else "BottomLayer"
        out.append(f'"{ref}","{val}","{layer}","{fp}","{x:.4f}","{y:.4f}","0",""')
    return "\n".join(out) + "\n"


def scenario_mixed(path: Path, with_pnp: bool = False) -> Path:
    """4 layers, SMD Top e Bot, PTH (DIP, barra de pinos, axiais, radial, borne, TO-220)."""
    top, bot, pth, npth, silk_rects, pnp = _mixed_geometry()
    W, H = 100.0, 80.0
    w = {k: GerberWriter(k, "mm") for k in ("GTL", "GBL", "G1", "G2", "GTP", "GBP", "GTO", "GBO", "GKO")}
    w["GKO"].rect_outline(W, H)
    for x, y, pw, ph in top:
        w["GTP"].flash_rect(x, y, pw, ph); w["GTL"].flash_rect(x, y, pw, ph)
    for x, y, pw, ph in bot:
        w["GBP"].flash_rect(x, y, pw, ph); w["GBL"].flash_rect(x, y, pw, ph)
    for x, y, d in pth:
        w["GTL"].flash_circle(x, y, d * 1.8); w["GBL"].flash_circle(x, y, d * 1.8)
    for item in silk_rects:
        _silk_shape(w["GTO"], item)
    w["G1"].region_rect(2, 2, 96, 76); w["G2"].region_rect(2, 2, 96, 76)
    vias = [(35.0, 20.0, 0.3), (50.0, 30.0, 0.3)]
    with zipfile.ZipFile(path, "w") as z:
        for ext, wr in w.items():
            z.writestr(f"Gerber/Mista.{ext}", wr.render())
        z.writestr("Drill/Mista-Plated.TXT", excellon_altium(pth + vias, True, "mm"))
        z.writestr("Drill/Mista-NonPlated.TXT", excellon_altium(npth, False, "mm"))
        if with_pnp:
            z.writestr("Assembly/Pick Place for Mista.txt", altium_pick_place(pnp))
    return path


# --------------------------------------------------------------------------- #
# Cenário 7: casos reportados pela VRI (placa Deltronix PCB0032) — PTH separados pela serigrafia
# --------------------------------------------------------------------------- #
PTH_CASES_EXPECTED = {"pth": 17}


def scenario_pth_cases(path: Path) -> Path:
    top, gtp, gto, gko = (GerberWriter(n, "mm") for n in ("TopLayer", "TopPaste", "TopOverlay", "KeepOut"))
    gko.rect_outline(100, 80)
    holes, vias, silk = [], [], []
    for k in range(7):                                   # TP1..TP7: 3 pinos, contornos empilhados
        y = 10 + k * 5.0
        holes += [(40 + j * 2.54, y, 1.0) for j in range(3)]
        silk.append((38.5, y - 2.5, 8.1, 5.0))
    for cx in (10.0, 16.5):                              # C60 e C51 radiais lado a lado
        holes += [(cx, 40.0, 0.8), (cx, 45.0, 0.8)]
        silk.append(("circle", cx, 42.5, 3.2))
    for x0 in (30.0, 36.5):                              # C52 e C61 radiais 2,5 mm
        holes += [(x0, 60.0, 0.8), (x0 + 2.5, 60.0, 0.8)]
        silk.append(("circle", x0 + 1.25, 60.0, 2.6))
    holes += [(60.0, 20.0, 0.8), (64.88, 20.0, 0.8)]     # Y1 cristal HC-49
    silk.append(("oval", 57.0, 18.0, 67.9, 22.0))
    vias += [(62.4, 24.6, 0.7)]                          # furo de passagem junto ao cristal
    gtp.flash_rect(69.0, 40.0, 1.0, 1.3); gtp.flash_rect(71.0, 40.0, 1.0, 1.3)   # C41 (SMD 0805)
    top.flash_rect(69.0, 40.0, 1.0, 1.3); top.flash_rect(71.0, 40.0, 1.0, 1.3)
    silk.append((67.8, 38.9, 4.4, 2.2))
    vias += [(70.0, 42.6, 0.7), (75.0, 45.0, 0.7), (80.0, 50.0, 0.7), (85.0, 50.0, 0.7)]  # passagens
    for x0 in (20.0, 30.0, 40.0, 50.0):                  # P13..P16: 4 pinos encostados
        holes += [(x0 + j * 2.54, 70.0, 1.0) for j in range(4)]
        silk.append((x0 - 1.5, 67.5, 10.62, 5.0))
    holes += [(12.0 + c * 2.54, 62.0 + r * 2.54, 1.0) for c in range(2) for r in range(5)]   # P6 2x5
    silk.append((10.5, 60.5, 5.6, 13.2))
    for x, y, d in holes:
        top.flash_circle(x, y, d * 1.8)
    for item in silk:
        _silk_shape(gto, item)
    with zipfile.ZipFile(path, "w") as z:
        for wr, ext in ((top, "GTL"), (gtp, "GTP"), (gto, "GTO"), (gko, "GKO")):
            z.writestr(f"Casos.{ext}", wr.render())
        # vias de passagem maiores que o limite padrão de via (0,5 mm): o drill não diferencia
        z.writestr("Casos-Plated.TXT", excellon_altium(holes + vias, True, "mm"))
    return path


if __name__ == "__main__":  # gera os exemplos para teste manual na interface
    out = Path("exemplos")
    out.mkdir(exist_ok=True)
    scenario_4l_smdtop_pth(out / "cenario1_4L_SMDtop_PTH.zip")
    scenario_2l_smd_both(out / "cenario2_2L_SMD_2lados.zip")
    scenario_1l_pth_only(out / "cenario3_1L_PTH.zip")
    scenario_l_shape(out / "cenario4_placa_em_L.zip")
    scenario_mixed(out / "cenario5_mista_sem_pickplace.zip")
    scenario_mixed(out / "cenario6_mista_com_pickplace.zip", with_pnp=True)
    print("Exemplos gerados em", out.resolve())
