"""Interface desktop (PySide6) do VRI GerberQuote."""
from __future__ import annotations

import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QFrame, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView,
                               QGridLayout, QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMessageBox,
                               QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter, QStackedWidget,
                               QTableWidget, QTableWidgetItem, QTabWidget, QToolButton, QVBoxLayout, QWidget)

from core.analyzer import AnalysisOptions, AnalysisResult, BoardData, ProjectAnalyzer, evaluate, fmt_mm
from core.classifier import DRILL_TYPES, GERBER_TYPES, PNP_TYPES, ClassifiedFile, classify_files
from core.extractor import ExtractedProject, extract_project
from core.packaging import BOX_MODELS, MATERIALS, PackagingSpec
from core.paths import resource_path
from core.timing import fmt_time, load_params, time_rows
from core.version import __version__
from export.pdf_generator import generate_package_pdf, generate_pdf
from render.gerber_renderer import render_all
from ui.time_params_dialog import TimeParamsDialog

LOGO_PATH = resource_path("assets/logo_vri.png")
BRAND = "#000099"

STYLE = f"""
QMainWindow, QWidget#central {{ background: #F4F6FB; }}
QFrame#sidebar, QFrame#summary {{ background: white; border-right: 1px solid #D9DDE8; }}
QFrame#summary {{ border-left: 1px solid #D9DDE8; border-right: none; }}
QLabel.section {{ color: white; background: {BRAND}; font-weight: bold; padding: 6px 8px;
                 border-radius: 3px; }}
QPushButton {{ padding: 6px 10px; border: 1px solid #B8BFD6; border-radius: 4px; background: #FFFFFF; }}
QPushButton:hover {{ background: #EEF0FA; }}
QPushButton#primary {{ background: {BRAND}; color: white; font-weight: bold; font-size: 13px;
                      padding: 11px; border: none; }}
QPushButton#primary:hover {{ background: #1A1AB8; }}
QPushButton#primary:disabled {{ background: #9A9AC8; }}
QTabWidget::pane {{ border: 1px solid #D9DDE8; background: white; }}
QTabBar::tab {{ padding: 7px 14px; background: #E6E9F3; border: 1px solid #D9DDE8; border-bottom: none;
               margin-right: 2px; }}
QTabBar::tab:selected {{ background: {BRAND}; color: white; font-weight: bold; }}
QHeaderView::section {{ background: {BRAND}; color: white; padding: 4px; border: none; font-weight: bold; }}
QSpinBox[manual="true"], QDoubleSpinBox[manual="true"] {{ background: #FFF3CD; }}
"""


# --------------------------------------------------------------------------- #
# Execução em segundo plano
# --------------------------------------------------------------------------- #
class _Signals(QObject):
    done = Signal(int, object)
    failed = Signal(int, str)


class _Task(QRunnable):
    def __init__(self, gen: int, fn):
        super().__init__()
        self.gen, self.fn, self.signals = gen, fn, _Signals()

    def run(self):
        try:
            self.signals.done.emit(self.gen, self.fn())
        except Exception as exc:  # noqa: BLE001 - erro exibido ao usuário
            traceback.print_exc()
            self.signals.failed.emit(self.gen, f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------- #
# Widgets de visualização
# --------------------------------------------------------------------------- #
class ImageView(QGraphicsView):
    """Visualizador com zoom (roda do mouse), arraste e duplo clique para ajustar."""

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.SmoothTransformation)
        self.scene().addItem(self._item)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QBrush(QColor("white")))
        self.setFrameShape(QFrame.NoFrame)
        self._auto_fit = True

    def set_png(self, png: bytes) -> None:
        pm = QPixmap()
        pm.loadFromData(png, "PNG")
        self._item.setPixmap(pm)
        self.scene().setSceneRect(self._item.boundingRect())
        self._auto_fit = True
        self.fit()

    def fit(self) -> None:
        if not self._item.pixmap().isNull():
            self.fitInView(self._item, Qt.KeepAspectRatio)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._auto_fit:
            self.fit()

    def wheelEvent(self, event):
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        self._auto_fit = False
        self.scale(factor, factor)

    def mouseDoubleClickEvent(self, event):
        self._auto_fit = True
        self.fit()


class ViewTab(QWidget):
    """Aba com banner de aviso + desenho, ou painel de 'SEM MONTAGEM'."""

    def __init__(self, placeholder: str = "Importe um projeto (.zip ou pasta) para visualizar."):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.banner = QLabel()
        self.banner.setAlignment(Qt.AlignCenter)
        self.banner.setWordWrap(True)
        self.banner.hide()
        lay.addWidget(self.banner)
        self.stack = QStackedWidget()
        self.view = ImageView()
        self.empty = QLabel(placeholder)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet("color:#777; font-size:14px;")
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self.view)
        lay.addWidget(self.stack, 1)

    def show_banner(self, text: str | None, alert: bool = False) -> None:
        if not text:
            self.banner.hide()
            return
        if alert:
            css = "background:#FDE2E1;color:#B00020;border:2px solid #B00020;"
        else:
            css = "background:#FFF3CD;color:#8A5A00;border:2px solid #C99A2E;"
        self.banner.setStyleSheet(css + "font-weight:bold;font-size:15px;padding:8px;border-radius:4px;")
        self.banner.setText(text)
        self.banner.show()

    def set_image(self, png: bytes | None, message: str | None = None, alert: bool = False) -> None:
        if png is None:
            self.empty.setText(message or "")
            self.empty.setStyleSheet(
                "color:#B00020;font-size:26px;font-weight:bold;" if alert else "color:#777;font-size:14px;")
            self.stack.setCurrentWidget(self.empty)
            self.show_banner(message, alert) if alert else self.show_banner(None)
        else:
            self.view.set_png(png)
            self.stack.setCurrentWidget(self.view)

    def reset(self) -> None:
        self.show_banner(None)
        self.empty.setText("Importe um projeto (.zip ou pasta) para visualizar.")
        self.empty.setStyleSheet("color:#777; font-size:14px;")
        self.stack.setCurrentWidget(self.empty)


def _section(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("class", "section")
    return lbl


# --------------------------------------------------------------------------- #
# Janela principal
# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"VRI GerberQuote {__version__} — VRI Indústria Eletrônica S.A.")
        if LOGO_PATH.exists():
            self.setWindowIcon(QIcon(str(LOGO_PATH)))
        self.resize(1600, 960)
        self.setStyleSheet(STYLE)
        self.setAcceptDrops(True)

        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)          # tarefas em série: sem condição de corrida
        self._gen = 0
        self._tasks: list[_Task] = []
        self.project: ExtractedProject | None = None
        self.classified: list[ClassifiedFile] = []
        self.analyzer = ProjectAnalyzer()
        self.time_params = load_params()
        self.board: BoardData | None = None
        self.result: AnalysisResult | None = None
        self._layers_manual = False
        self._height_manual = False
        self._debounce = QTimer(self, singleShot=True, interval=200)
        self._debounce.timeout.connect(self._reevaluate)

        central = QWidget(objectName="central")
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())
        root.addWidget(self._build_center(), 1)
        root.addWidget(self._build_summary())
        self.setCentralWidget(central)
        self._build_menu()
        self.statusBar().showMessage("Pronto. Importe um .zip ou uma pasta de projeto "
                                     "(ou arraste-os para a janela).")
        self._set_enabled(False)

    # ------------------------------------------------------------------ UI --
    def _build_menu(self):
        m = self.menuBar().addMenu("&Arquivo")
        for text, slot, sc in (("Importar .ZIP...", self.import_zip, "Ctrl+O"),
                               ("Importar Pasta...", self.import_folder, "Ctrl+Shift+O"),
                               ("Exportar PDF Técnico...", self.export_pdf, "Ctrl+E")):
            a = QAction(text, self, shortcut=sc)
            a.triggered.connect(slot)
            m.addAction(a)
        m.addSeparator()
        q = QAction("Sair", self, shortcut="Ctrl+Q")
        q.triggered.connect(self.close)
        m.addAction(q)

    def _build_sidebar(self) -> QWidget:
        frame = QFrame(objectName="sidebar")
        frame.setFixedWidth(400)
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        logo_box = QLabel()
        logo_box.setAlignment(Qt.AlignCenter)
        logo_box.setStyleSheet(f"background:{BRAND}; padding:10px;")
        logo_box.setFixedHeight(120)
        pm = QPixmap(str(LOGO_PATH)) if LOGO_PATH.exists() else QPixmap()
        if not pm.isNull():
            logo_box.setPixmap(pm.scaled(360, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            logo_box.setText("VRI")
            logo_box.setStyleSheet(f"background:{BRAND}; color:white; font-size:40px; font-weight:bold;")
        outer.addWidget(logo_box)
        sub = QLabel("GerberQuote · Análise Técnica para Orçamento")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet(f"background:{BRAND}; color:#C9CCFF; padding-bottom:8px; font-size:11px;")
        outer.addWidget(sub)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        lay.addWidget(_section("1. Importação"))
        row = QHBoxLayout()
        b_zip = QPushButton("📦  Importar .ZIP")
        b_zip.clicked.connect(self.import_zip)
        b_dir = QPushButton("📁  Importar Pasta")
        b_dir.clicked.connect(self.import_folder)
        row.addWidget(b_zip)
        row.addWidget(b_dir)
        lay.addLayout(row)
        self.lbl_source = QLabel("Nenhum projeto carregado.")
        self.lbl_source.setWordWrap(True)
        self.lbl_source.setStyleSheet("color:#555; font-size:11px;")
        lay.addWidget(self.lbl_source)

        lay.addWidget(_section("2. Arquivos detectados (confirme o tipo)"))
        self.tbl_files = QTableWidget(0, 2)
        self.tbl_files.setHorizontalHeaderLabels(["Arquivo", "Tipo de camada"])
        self.tbl_files.verticalHeader().hide()
        self.tbl_files.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tbl_files.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tbl_files.setSelectionMode(QAbstractItemView.NoSelection)
        self.tbl_files.setMinimumHeight(250)
        lay.addWidget(self.tbl_files, 1)

        lay.addWidget(_section("3. Parâmetros do orçamento"))
        grid = QGridLayout()
        self.chk_pth_top = QCheckBox("PTH no Top")
        self.chk_pth_top.setChecked(True)
        self.chk_pth_bot = QCheckBox("PTH no Bot")
        for c in (self.chk_pth_top, self.chk_pth_bot):
            c.setStyleSheet("font-weight:bold;")
            c.toggled.connect(self._schedule)
        grid.addWidget(QLabel("Lado de inserção PTH:"), 0, 0, 1, 3)
        grid.addWidget(self.chk_pth_top, 1, 0)
        grid.addWidget(self.chk_pth_bot, 1, 1)

        self.spin_layers = QSpinBox(minimum=1, maximum=64)
        self.spin_layers.valueChanged.connect(self._layers_edited)
        self.btn_layers_auto = self._auto_button(self._reset_layers)
        grid.addWidget(QLabel("Número de Layers:"), 2, 0)
        grid.addWidget(self.spin_layers, 2, 1)
        grid.addWidget(self.btn_layers_auto, 2, 2)

        self.spin_height = QDoubleSpinBox(minimum=1.0, maximum=500.0, decimals=1, singleStep=1.0, suffix=" mm")
        self.spin_height.valueChanged.connect(self._height_edited)
        self.btn_height_auto = self._auto_button(self._reset_height)
        grid.addWidget(QLabel("Altura Est. Montagem (mm):"), 3, 0)
        grid.addWidget(self.spin_height, 3, 1)
        grid.addWidget(self.btn_height_auto, 3, 2)

        self.spin_via = QDoubleSpinBox(minimum=0.0, maximum=2.0, decimals=2, singleStep=0.05, suffix=" mm")
        self.spin_via.setValue(AnalysisOptions().via_max_diameter)
        self.spin_via.setToolTip("Furos metalizados com diâmetro menor ou igual a este valor são "
                                 "considerados vias (não contam como montagem PTH). Use 0 para "
                                 "considerar todos os furos metalizados como PTH de componente.")
        self.spin_via.valueChanged.connect(self._schedule)
        grid.addWidget(QLabel("Ø máx. de via (mm):"), 4, 0)
        grid.addWidget(self.spin_via, 4, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)

        lay.addWidget(_section("4. Embalagem (cotação com fabricante)"))
        pk = QFormLayout()
        self.cmb_box = QComboBox()
        for name, desc in BOX_MODELS.items():
            self.cmb_box.addItem(name, name)
            self.cmb_box.setItemData(self.cmb_box.count() - 1, desc, Qt.ToolTipRole)
        self.cmb_material = QComboBox()
        self.cmb_material.addItems(MATERIALS)
        for cb in (self.cmb_box, self.cmb_material):
            cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            cb.setMinimumContentsLength(18)
        self.spin_qty = QSpinBox(minimum=0, maximum=10_000_000, singleStep=100)
        self.spin_qty.setSpecialValueText("A definir")
        for w in (self.cmb_box, self.cmb_material):
            w.currentIndexChanged.connect(self._schedule)
        self.spin_qty.valueChanged.connect(self._schedule)
        pk.addRow("Modelo da caixa:", self.cmb_box)
        pk.addRow("Material:", self.cmb_material)
        pk.addRow("Quantidade de caixas:", self.spin_qty)
        lay.addLayout(pk)
        self.btn_export_pkg = QPushButton("📦  Exportar Desenho de Embalagem (PDF)")
        self.btn_export_pkg.setToolTip("PDF avulso, em A4 paisagem, para enviar ao fabricante de caixas")
        self.btn_export_pkg.clicked.connect(self.export_package_pdf)
        lay.addWidget(self.btn_export_pkg)

        lay.addWidget(_section("5. Componentes e tempos de montagem"))
        tg = QGridLayout()
        self.spin_lot = QSpinBox(minimum=1, maximum=10_000_000, singleStep=50, suffix=" placas")
        self.spin_lot.setValue(100)
        self.spin_lot.valueChanged.connect(self._schedule)
        tg.addWidget(QLabel("Tamanho do lote:"), 0, 0)
        tg.addWidget(self.spin_lot, 0, 1)
        self.count_spins = {}
        self._count_manual = {}
        for row, (key, label) in enumerate((("smd_top", "Comp. SMD Top:"), ("smd_bot", "Comp. SMD Bot:"),
                                            ("pth", "Comp. PTH:")), start=1):
            sp = QSpinBox(minimum=0, maximum=100000)
            sp.valueChanged.connect(lambda _v, k=key: self._count_edited(k))
            bt = self._auto_button(lambda _=False, k=key: self._reset_count(k))
            tg.addWidget(QLabel(label), row, 0)
            tg.addWidget(sp, row, 1)
            tg.addWidget(bt, row, 2)
            self.count_spins[key] = sp
            self._count_manual[key] = False
        tg.setColumnStretch(1, 1)
        lay.addLayout(tg)
        self.btn_time_params = QPushButton("⚙  Parâmetros de tempo (Fuji AIM / PTH IPC)…")
        self.btn_time_params.clicked.connect(self.edit_time_params)
        lay.addWidget(self.btn_time_params)

        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        btn_box = QWidget()
        bl = QVBoxLayout(btn_box)
        bl.setContentsMargins(12, 8, 12, 12)
        self.btn_export = QPushButton("📄  EXPORTAR PDF TÉCNICO CONSOLIDADO", objectName="primary")
        self.btn_export.clicked.connect(self.export_pdf)
        bl.addWidget(self.btn_export)
        outer.addWidget(btn_box)
        return frame

    def _auto_button(self, slot) -> QToolButton:
        b = QToolButton(text="↺ auto")
        b.setToolTip("Voltar ao valor calculado automaticamente")
        b.clicked.connect(slot)
        return b

    def _build_center(self) -> QWidget:
        self.tabs = QTabWidget()
        self.tab_overview = ViewTab()
        self.tab_top = ViewTab()
        self.tab_bot = ViewTab()
        self.tab_dimdrill = ViewTab()
        self.tab_pkg = ViewTab()
        self.tabs.addTab(self.tab_overview, "1 · Vista Geral")
        self.tabs.addTab(self.tab_top, "2 · Montagem TOP")
        self.tabs.addTab(self.tab_bot, "3 · Montagem BOT")
        self.tabs.addTab(self.tab_dimdrill, "4 · Dimensional e Furação")
        self.tabs.addTab(self.tab_pkg, "5 · Embalagem")
        self.tabs.addTab(self._build_times_tab(), "6 · Componentes e Tempos")
        wrap = QWidget()
        l = QVBoxLayout(wrap)
        l.setContentsMargins(8, 8, 8, 8)
        l.addWidget(self.tabs)
        hint = QLabel("Roda do mouse: zoom · arrastar: mover · duplo clique: ajustar à tela")
        hint.setStyleSheet("color:#888; font-size:11px;")
        l.addWidget(hint)
        return wrap

    def _build_summary(self) -> QWidget:
        outer = QScrollArea(objectName="summary")
        outer.setWidgetResizable(True)
        outer.setFixedWidth(345)
        outer.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        frame = QFrame(objectName="summary")
        outer.setWidget(frame)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(12, 12, 12, 12)
        head = QLabel("RESUMO COMERCIAL")
        head.setAlignment(Qt.AlignCenter)
        head.setStyleSheet(f"background:{BRAND}; color:white; font-weight:bold; font-size:15px; padding:10px;"
                           "border-radius:4px;")
        lay.addWidget(head)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        form.setVerticalSpacing(9)
        self.sum = {}
        for key, label in (("layers", "Número de layers"), ("shape", "Formato"), ("dims", "Dimensões X × Y"),
                           ("area", "Área"), ("c_smd_top", "Comp. SMD Top"), ("c_smd_bot", "Comp. SMD Bot"),
                           ("c_pth", "Comp. PTH"), ("smd_top", "Pads SMD Top"), ("smd_bot", "Pads SMD Bot"),
                           ("pth", "Furos PTH"), ("npth", "Furos NPTH"), ("height", "Altura est. (Z)"),
                           ("t_smd", "Tempo SMD/placa"), ("t_pth", "Tempo PTH/placa"), ("t_lot", "Tempo do lote")):
            v = QLabel("—")
            v.setStyleSheet("font-weight:bold; font-size:13px;")
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            v.setWordWrap(True)
            form.addRow(QLabel(label + ":"), v)
            self.sum[key] = v
        lay.addLayout(form)
        lay.addSpacing(6)
        lay.addWidget(QLabel("<b>Status de montagem</b>"))
        self.lbl_status_top = QLabel("—")
        self.lbl_status_bot = QLabel("—")
        for l in (self.lbl_status_top, self.lbl_status_bot):
            l.setWordWrap(True)
            l.setStyleSheet("padding:6px; border-radius:4px; background:#EEE;")
            lay.addWidget(l)
        lay.addSpacing(6)
        pk_head = QLabel("EMBALAGEM SUGERIDA (X × Y × Z)")
        pk_head.setStyleSheet(f"color:{BRAND}; font-weight:bold;")
        lay.addWidget(pk_head)
        self.lbl_package = QLabel("—")
        self.lbl_package.setAlignment(Qt.AlignCenter)
        self.lbl_package.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_package.setStyleSheet(f"font-size:22px; font-weight:bold; color:{BRAND}; border:2px solid {BRAND};"
                                       "border-radius:6px; padding:10px; background:#F0F1FF;")
        lay.addWidget(self.lbl_package)
        note = QLabel("Margem ESD/plástico bolha: +20 mm em X e Y, +10 mm em Z.")
        note.setStyleSheet("color:#666; font-size:10px;")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addSpacing(6)
        lay.addWidget(QLabel("<b>Observações</b>"))
        self.lbl_warn = QLabel("—")
        self.lbl_warn.setWordWrap(True)
        self.lbl_warn.setAlignment(Qt.AlignTop)
        self.lbl_warn.setStyleSheet("color:#8A5A00; font-size:11px;")
        self.lbl_warn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        lay.addWidget(self.lbl_warn, 1)
        return outer

    def _build_times_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.lbl_comp_source = QLabel("Importe um projeto para estimar componentes e tempos.")
        self.lbl_comp_source.setWordWrap(True)
        self.lbl_comp_source.setStyleSheet("font-size:12px; padding:4px;")
        lay.addWidget(self.lbl_comp_source)
        split = QSplitter(Qt.Vertical)
        self.tbl_times = QTableWidget(0, 3)
        self.tbl_times.setHorizontalHeaderLabels(["Etapa", "Base de cálculo", "Tempo"])
        self.tbl_comps = QTableWidget(0, 7)
        self.tbl_comps.setHorizontalHeaderLabels(["Designador", "Lado", "Tecnologia", "Categoria",
                                                  "Terminais", "X / Y (mm)", "Footprint / Valor"])
        for t in (self.tbl_times, self.tbl_comps):
            t.verticalHeader().hide()
            t.setEditTriggers(QAbstractItemView.NoEditTriggers)
            t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
            t.horizontalHeader().setStretchLastSection(True)
            t.setAlternatingRowColors(True)
        split.addWidget(self.tbl_times)
        split.addWidget(self.tbl_comps)
        split.setSizes([420, 300])
        lay.addWidget(split, 1)
        return w

    # -------------------------------------------------------------- ações --
    def _set_enabled(self, on: bool):
        for w in (self.btn_export, self.btn_export_pkg, self.cmb_box, self.cmb_material, self.spin_qty,
                  self.chk_pth_top, self.chk_pth_bot, self.spin_layers,
                  self.spin_height, self.spin_via, self.btn_layers_auto, self.btn_height_auto,
                  self.spin_lot, self.btn_time_params, *self.count_spins.values()):
            w.setEnabled(on)

    def import_zip(self):
        path, _ = QFileDialog.getOpenFileName(self, "Importar projeto (.zip)", "", "Arquivo ZIP (*.zip)")
        if path:
            self.load_source(path)

    def import_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Selecionar pasta raiz do projeto")
        if path:
            self.load_source(path)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            self.load_source(urls[0].toLocalFile())

    def _submit(self, fn, on_done, busy: str, supersede: bool = True):
        """Executa ``fn`` em segundo plano. Com ``supersede``, resultados de tarefas
        anteriores ainda pendentes são descartados (só o mais recente é exibido)."""
        if supersede:
            self._gen += 1
        gen = self._gen if supersede else -1
        task = _Task(gen, fn)
        task.setAutoDelete(False)
        self._tasks.append(task)

        def done(g, payload):
            self._tasks.remove(task) if task in self._tasks else None
            if g == self._gen or g == -1:
                on_done(payload)
                self.statusBar().showMessage("Pronto.", 4000)

        def failed(g, msg):
            self._tasks.remove(task) if task in self._tasks else None
            self.statusBar().showMessage("Erro: " + msg)
            if g == self._gen or g == -1:
                QMessageBox.critical(self, "VRI GerberQuote", f"Falha no processamento:\n{msg}")

        task.signals.done.connect(done)
        task.signals.failed.connect(failed)
        self.statusBar().showMessage(busy)
        self.pool.start(task)

    def _options(self) -> AnalysisOptions:
        return AnalysisOptions(
            pth_top=self.chk_pth_top.isChecked(), pth_bot=self.chk_pth_bot.isChecked(),
            layers_override=self.spin_layers.value() if self._layers_manual else None,
            height_override=self.spin_height.value() if self._height_manual else None,
            via_max_diameter=self.spin_via.value(),
            time_params=self.time_params, lot_qty=self.spin_lot.value(),
            smd_top_override=self.count_spins["smd_top"].value() if self._count_manual["smd_top"] else None,
            smd_bot_override=self.count_spins["smd_bot"].value() if self._count_manual["smd_bot"] else None,
            pth_override=self.count_spins["pth"].value() if self._count_manual["pth"] else None)

    def _pack_spec(self, project: str | None = None) -> PackagingSpec:
        name = project if project is not None else (self.project.project_name if self.project else "")
        return PackagingSpec(model=self.cmb_box.currentData(), material=self.cmb_material.currentText(),
                             quantity=self.spin_qty.value(), project=name)

    def load_source(self, path: str):
        old = self.project
        self._layers_manual = self._height_manual = False
        for k in self._count_manual:
            self._count_manual[k] = False
            self._mark(self.count_spins[k], False)
        self.analyzer = ProjectAnalyzer()
        analyzer = self.analyzer
        opts = self._options()
        pack = self._pack_spec(Path(path).stem if Path(path).suffix else Path(path).name)

        def job():
            project = extract_project(path)
            classified = classify_files(project.files)
            board = analyzer.load(classified)
            result = evaluate(board, opts)
            return project, classified, board, result, render_all(board, result, packaging=pack)

        def done(payload):
            if old is not None and old is not payload[0]:
                old.cleanup()
            self.project, self.classified, self.board, self.result, images = payload
            self._fill_files()
            self._show(images)
            self._set_enabled(True)
            n_ign = len(self.project.ignored)
            self.lbl_source.setText(f"<b>{self.project.project_name}</b><br>{len(self.classified)} arquivos "
                                    f"Gerber/Drill detectados · {n_ign} ignorados"
                                    + (f"<br><span style='color:#999'>Ignorados: "
                                       f"{', '.join(Path(i).name for i in self.project.ignored[:8])}"
                                       f"{' …' if n_ign > 8 else ''}</span>" if n_ign else ""))
            if not self.classified:
                QMessageBox.warning(self, "VRI GerberQuote",
                                    "Nenhum arquivo Gerber ou Excellon foi encontrado nesta origem.")

        self._submit(job, done, f"Importando e analisando {Path(path).name}…")

    def _fill_files(self):
        self.tbl_files.setRowCount(0)
        for row, cf in enumerate(self.classified):
            self.tbl_files.insertRow(row)
            item = QTableWidgetItem(cf.source.relpath)
            item.setToolTip(f"{cf.source.relpath}\nCritério: {cf.reason}")
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.tbl_files.setItem(row, 0, item)
            combo = QComboBox()
            options = {"excellon": DRILL_TYPES, "pnp": PNP_TYPES}.get(cf.source.fmt, GERBER_TYPES)
            for t in options:
                combo.addItem(t.value, t)
            combo.setCurrentIndex(options.index(cf.layer_type) if cf.layer_type in options else 0)
            combo.currentIndexChanged.connect(lambda _i, r=row, c=combo: self._type_changed(r, c))
            self.tbl_files.setCellWidget(row, 1, combo)

    def _type_changed(self, row: int, combo: QComboBox):
        self.classified[row].layer_type = combo.currentData()
        self.classified[row].reason = "definido manualmente"
        self._layers_manual = False
        classified, analyzer, opts = list(self.classified), self.analyzer, self._options()
        pack = self._pack_spec()

        def job():
            board = analyzer.load(classified)
            result = evaluate(board, opts)
            return board, result, render_all(board, result, packaging=pack)

        def done(payload):
            self.board, self.result, images = payload
            self._show(images)

        self._submit(job, done, "Reprocessando camadas…")

    def _schedule(self, *_):
        if self.board is not None:
            self._debounce.start()

    def _reevaluate(self):
        board, opts, pack = self.board, self._options(), self._pack_spec()

        def job():
            result = evaluate(board, opts)
            return result, render_all(board, result, packaging=pack)

        def done(payload):
            self.result, images = payload
            self._show(images)

        self._submit(job, done, "Recalculando…")

    def _layers_edited(self, _v):
        if self.spin_layers.signalsBlocked():
            return
        self._layers_manual = True
        self._mark(self.spin_layers, True)
        self._schedule()

    def _height_edited(self, _v):
        if self.spin_height.signalsBlocked():
            return
        self._height_manual = True
        self._mark(self.spin_height, True)
        self._schedule()

    def _reset_layers(self):
        self._layers_manual = False
        self._mark(self.spin_layers, False)
        self._schedule()

    def _count_edited(self, key: str):
        if self.count_spins[key].signalsBlocked():
            return
        self._count_manual[key] = True
        self._mark(self.count_spins[key], True)
        self._schedule()

    def _reset_count(self, key: str):
        self._count_manual[key] = False
        self._mark(self.count_spins[key], False)
        self._schedule()

    def edit_time_params(self):
        dlg = TimeParamsDialog(self.time_params, self)
        if dlg.exec():
            self.time_params = dlg.params
            self._schedule()

    def _reset_height(self):
        self._height_manual = False
        self._mark(self.spin_height, False)
        self._schedule()

    @staticmethod
    def _mark(w, manual: bool):
        w.setProperty("manual", "true" if manual else "false")
        w.style().unpolish(w)
        w.style().polish(w)

    # ------------------------------------------------------------ exibição --
    def _show(self, images: dict):
        r = self.result
        if r is None:
            return
        for spin, val, manual in ((self.spin_layers, r.layers, self._layers_manual),
                                  (self.spin_height, r.assembly_height, self._height_manual)):
            if not manual:
                spin.blockSignals(True)
                spin.setValue(val)
                spin.blockSignals(False)
                self._mark(spin, False)

        auto_counts = {"smd_top": r.components.smd_top if r.components else 0,
                       "smd_bot": r.components.smd_bot if r.components else 0,
                       "pth": r.components.pth if r.components else 0}
        for k, sp in self.count_spins.items():
            if not self._count_manual[k]:
                sp.blockSignals(True)
                sp.setValue(auto_counts[k])
                sp.blockSignals(False)
                self._mark(sp, False)
        self._fill_times(r)

        self.tab_overview.set_image(images["overview"])
        for tab, status, key in ((self.tab_top, r.top, "top"), (self.tab_bot, r.bot, "bot")):
            if images[key] is None:
                tab.set_image(None, status.message, alert=True)
            else:
                tab.set_image(images[key])
                tab.show_banner(status.message if status.is_partial else None, alert=False)
        self.tab_dimdrill.set_image(images["dimdrill"])
        self.tab_pkg.set_image(images["package"])
        self.tabs.setTabText(1, "2 · Montagem TOP" + ("" if r.top.has_view else " ⚠"))
        self.tabs.setTabText(2, "3 · Montagem BOT" + ("" if r.bot.has_view else " ⚠"))

        s = self.sum
        s["layers"].setText(str(r.layers) + ("" if r.layers == r.layers_auto else f"  (auto: {r.layers_auto})"))
        s["shape"].setText(r.shape)
        tag = "" if (r.components and r.components.exact) else "  (estimado)"
        s["c_smd_top"].setText(f"{r.comp_smd_top}{tag}")
        s["c_smd_bot"].setText(f"{r.comp_smd_bot}{tag}")
        s["c_pth"].setText(f"{r.comp_pth}{tag}")
        if r.times:
            s["t_smd"].setText(fmt_time(r.times.smd_unit_s))
            s["t_pth"].setText(fmt_time(r.times.pth_unit_s))
            s["t_lot"].setText(f"{r.times.lot_total_h:.2f} h ({r.times.lot_qty} placas)".replace(".", ",", 1))
        s["dims"].setText(r.board_text)
        s["area"].setText(f"{fmt_mm(r.area_cm2)} cm²" + (f"\n(real {fmt_mm(r.real_area_cm2)} cm²)"
                                                          if abs(r.real_area_cm2 - r.area_cm2) > 0.05 else ""))
        s["smd_top"].setText(f"{r.smd_top}" + (f"  ({r.smd_top_source})" if r.smd_top_source != "Paste" else ""))
        s["smd_bot"].setText(f"{r.smd_bot}" + (f"  ({r.smd_bot_source})" if r.smd_bot_source != "Paste" else ""))
        s["pth"].setText(f"{r.pth_total}  (comp. {r.pth_component} · vias {r.via_count})")
        s["npth"].setText(str(r.npth_count))
        s["height"].setText(f"{fmt_mm(r.assembly_height, 1)} mm" + ("  (manual)" if self._height_manual else ""))
        for lbl, st in ((self.lbl_status_top, r.top), (self.lbl_status_bot, r.bot)):
            lbl.setText(st.message)
            if not st.has_assembly:
                css = "background:#FDE2E1;color:#B00020;border:1px solid #B00020;"
            elif st.is_partial:
                css = "background:#FFF3CD;color:#8A5A00;border:1px solid #C99A2E;"
            else:
                css = "background:#E7F5EC;color:#0B6B2E;border:1px solid #0B8F3A;"
            lbl.setStyleSheet(css + "padding:6px;border-radius:4px;font-weight:bold;")
        self.lbl_package.setText(r.package_text)
        self.lbl_warn.setText("\n".join(f"• {w}" for w in r.warnings) or "Nenhuma.")

    # ------------------------------------------------------------- export --
    def export_pdf(self):
        if self.result is None or self.board is None:
            QMessageBox.information(self, "VRI GerberQuote", "Importe um projeto antes de exportar.")
            return
        name = self.project.project_name if self.project else "projeto"
        path, _ = QFileDialog.getSaveFileName(self, "Exportar PDF Técnico Consolidado",
                                              str(Path.home() / f"{name}_Analise_VRI.pdf"), "PDF (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        board, result, classified = self.board, self.result, list(self.classified)
        sides = (self.chk_pth_top.isChecked(), self.chk_pth_bot.isChecked())
        pack = self._pack_spec()
        tparams = self.time_params

        def job():
            return generate_pdf(path, board, result, classified, name, pth_sides=sides, packaging=pack,
                                time_params=tparams)

        def done(report):
            QMessageBox.information(self, "VRI GerberQuote",
                                    f"PDF exportado com sucesso ({report.pages} páginas):\n{report.path}")

        self._submit(job, done, "Gerando PDF…", supersede=False)

    def _fill_times(self, r):
        t = r.times
        comps = r.components
        if comps is None or t is None:
            return
        src = ("<b>Fonte da contagem:</b> " + comps.source
               + ("" if comps.exact else " — <span style='color:#8A5A00'>confira as caixas tracejadas nas abas "
                  "de montagem; corrija os totais no painel lateral se necessário.</span>"))
        if comps.excluded:
            src += f"<br>Não montados (excluídos): {', '.join(comps.excluded[:15])}"
        self.lbl_comp_source.setText(src)
        rows = time_rows(t, self.time_params)
        self.tbl_times.setRowCount(len(rows))
        for i, row in enumerate(rows):
            bold = row[0].startswith(("TOTAL", "SMD TOP — tempo", "SMD BOT — tempo", "PTH — tempo"))
            for j, txt in enumerate(row):
                it = QTableWidgetItem(txt)
                if bold:
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                if row[0].startswith("TOTAL"):
                    it.setForeground(QColor(BRAND))
                self.tbl_times.setItem(i, j, it)
        items = sorted(comps.components, key=lambda c: (c.kind, c.side, c.designator or "~", c.x))
        self.tbl_comps.setRowCount(len(items))
        for i, c in enumerate(items):
            vals = [c.designator or f"#{i + 1}", c.side, c.kind, c.category, str(c.terminals),
                    f"{c.x:.2f} / {c.y:.2f}", " ".join(v for v in (c.footprint, c.value) if v)]
            for j, txt in enumerate(vals):
                self.tbl_comps.setItem(i, j, QTableWidgetItem(txt))

    def export_package_pdf(self):
        if self.result is None:
            QMessageBox.information(self, "VRI GerberQuote", "Importe um projeto antes de exportar.")
            return
        name = self.project.project_name if self.project else "projeto"
        path, _ = QFileDialog.getSaveFileName(self, "Exportar Desenho de Embalagem",
                                              str(Path.home() / f"{name}_Embalagem_VRI.pdf"), "PDF (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        result, spec = self.result, self._pack_spec()

        def done(report):
            QMessageBox.information(self, "VRI GerberQuote", f"Desenho de embalagem exportado:\n{report.path}")

        self._submit(lambda: generate_package_pdf(path, result, spec), done, "Gerando desenho de embalagem…",
                     supersede=False)

    def closeEvent(self, event):
        self.pool.waitForDone(5000)
        if self.project:
            self.project.cleanup()
        super().closeEvent(event)
