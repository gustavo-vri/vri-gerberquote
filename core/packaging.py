"""Especificação da embalagem (caixa de papelão) para cotação com fabricante.

As medidas calculadas são **internas** (C x L x A), já com a margem de proteção
ESD/plástico bolha definida em ``core.analyzer``. Os acréscimos de vinco/espessura
da onda são responsabilidade do fabricante da caixa.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from core.analyzer import PACKAGE_MARGIN_X, PACKAGE_MARGIN_Z, AnalysisResult

BOX_MODELS = {
    "FEFCO 0201": "Caixa normal (abas inferiores e superiores que se encontram ao centro)",
    "FEFCO 0427": "Caixa corte e vinco tipo maleta (tampa integrada, fechamento por encaixe)",
}
MATERIALS = [
    "Papelão ondulado parede simples - onda B (~3 mm)",
    "Papelão ondulado parede simples - onda C (~4 mm)",
    "Papelão ondulado parede simples - onda E (~1,5 mm)",
    "Papelão ondulado parede dupla - onda BC (~7 mm)",
]
GLUE_FLAP_0201 = 30.0  # mm, aba de colagem usual (referência para a planificação)


@dataclass
class PackagingSpec:
    model: str = "FEFCO 0201"
    material: str = MATERIALS[0]
    quantity: int = 0                # 0 = a definir
    project: str = ""
    notes: str = ""


@dataclass
class BoxDimensions:
    length: float      # C (maior dimensão interna)
    width: float       # L
    height: float      # A
    pcb_x: float
    pcb_y: float
    assembly_height: float
    margin_xy: float   # folga por lado em X/Y
    margin_z: float    # folga acima/abaixo

    @property
    def text(self) -> str:
        return f"{self.length:.0f} x {self.width:.0f} x {self.height:.0f} mm"

    def blank_0201(self) -> tuple[float, float]:
        """Dimensões aproximadas da chapa planificada FEFCO 0201 (sem acréscimos de vinco)."""
        return (2 * self.length + 2 * self.width + GLUE_FLAP_0201, self.height + self.width)


def box_dimensions(result: AnalysisResult) -> BoxDimensions:
    x, y, z = result.package
    bx, by = result.board_x, result.board_y
    if y > x:  # C sempre a maior medida
        x, y, bx, by = y, x, by, bx
    up = lambda v: float(math.ceil(v - 0.01))  # nunca arredonda para menos  # noqa: E731
    return BoxDimensions(up(x), up(y), up(z), bx, by, result.assembly_height,
                         PACKAGE_MARGIN_X / 2, PACKAGE_MARGIN_Z / 2)
