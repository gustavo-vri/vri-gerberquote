"""Diálogo de parâmetros de tempo (Fuji AIM / PTH manual IPC), salvos em ~/.vri_gerberquote."""
from __future__ import annotations

from dataclasses import fields

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QVBoxLayout)

from core.timing import CONFIG_FILE, LABELS, TimeParams, save_params

SMD_FIELDS = ["machine", "t_chip_s", "t_small_s", "t_ic_s", "t_handling_side_s", "t_print_side_s",
              "conveyor_m_min", "board_gap_mm", "oven_length_m", "t_inspect_smd_s",
              "setup_program_min", "setup_feeder_min", "first_article_min"]
PTH_FIELDS = ["t_pth_prep_s", "t_pth_insert_s", "t_pth_solder_joint_s", "t_pth_cut_joint_s",
              "t_pth_inspect_s", "t_pth_handling_s", "setup_pth_min"]


class TimeParamsDialog(QDialog):
    def __init__(self, params: TimeParams, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Parâmetros de tempo de montagem")
        self.params = params
        self.widgets = {}
        lay = QVBoxLayout(self)
        info = QLabel("Valores de referência iniciais: calibre com os tempos reais medidos na VRI.\n"
                      "Tempos SMD são EFETIVOS por componente (já com trocas de bico, visão e eficiência "
                      "da Fuji AIM).")
        info.setWordWrap(True)
        info.setStyleSheet("color:#555;")
        lay.addWidget(info)
        row = QHBoxLayout()
        for title, names in (("SMD — Pick & Place + Refusão", SMD_FIELDS),
                             ("PTH — Montagem manual (IPC-A-610 / J-STD-001)", PTH_FIELDS)):
            box = QGroupBox(title)
            form = QFormLayout(box)
            for n in names:
                val = getattr(params, n)
                if isinstance(val, str):
                    w = QLineEdit(val)
                else:
                    w = QDoubleSpinBox(minimum=0.0, maximum=100000.0, decimals=2, singleStep=0.1)
                    w.setValue(val)
                self.widgets[n] = w
                form.addRow(LABELS.get(n, n) + ":", w)
            row.addWidget(box)
        lay.addLayout(row)
        foot = QLabel(f"Salvo em: {CONFIG_FILE}")
        foot.setStyleSheet("color:#888; font-size:10px;")
        lay.addWidget(foot)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setText("Salvar")
        bb.button(QDialogButtonBox.Cancel).setText("Cancelar")
        reset = QPushButton("Restaurar padrões")
        reset.clicked.connect(self._reset)
        bb.addButton(reset, QDialogButtonBox.ResetRole)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _reset(self):
        d = TimeParams()
        for n, w in self.widgets.items():
            v = getattr(d, n)
            w.setText(v) if isinstance(w, QLineEdit) else w.setValue(v)

    def _save(self):
        p = TimeParams()
        for f in fields(TimeParams):
            w = self.widgets.get(f.name)
            if w is None:
                continue
            setattr(p, f.name, w.text().strip() or "Fuji AIM" if isinstance(w, QLineEdit) else float(w.value()))
        self.params = p
        try:
            save_params(p)
        except OSError:
            pass
        self.accept()
