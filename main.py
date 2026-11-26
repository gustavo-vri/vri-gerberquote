"""VRI GerberQuote — VRI Indústria Eletrônica S.A.

Uso:
    python main.py                          # abre a interface
    python main.py projeto.zip              # abre a interface já carregando o projeto
    python main.py projeto.zip --pdf saida.pdf [--pth-bot] [--no-pth-top]
                                            # modo linha de comando (sem interface)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# No executável de janela (Windows/macOS) não existe console: evita erro ao usar print().
if sys.stdout is None or sys.stderr is None:
    import os

    _null = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = sys.stdout or _null
    sys.stderr = sys.stderr or _null


def run_cli(args) -> int:
    from core.analyzer import AnalysisOptions, analyze_project
    from core.timing import load_params
    from export.pdf_generator import generate_pdf, summary_rows

    tparams = load_params()
    opts = AnalysisOptions(pth_top=not args.no_pth_top, pth_bot=args.pth_bot,
                           layers_override=args.layers, height_override=args.height,
                           time_params=tparams, lot_qty=args.lote)
    session = analyze_project(args.source, opts)
    try:
        name = session.project.project_name
        print(f"\n=== VRI GerberQuote — {name} ===")
        for cf in session.classified:
            print(f"  {cf.source.relpath:<45} {cf.layer_type.value:<28} ({cf.reason})")
        print()
        for k, v in summary_rows(session.result, name):
            print(f"  {k:<32} {v}")
        for w in session.result.warnings:
            print(f"  ! {w}")
        report = generate_pdf(args.pdf, session.board, session.result, session.classified, name,
                              pth_sides=(opts.pth_top, opts.pth_bot), time_params=tparams)
        print(f"\nPDF gerado: {report.path} ({report.pages} páginas; desenhos: {', '.join(report.sections)})")
    finally:
        session.project.cleanup()
    return 0


def run_gui(source: str | None) -> int:
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    from ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("VRI GerberQuote")
    app.setOrganizationName("VRI Indústria Eletrônica S.A.")
    app.setStyle("Fusion")
    if sys.platform == "win32":
        app.setFont(QFont("Segoe UI", 9))
    from core.paths import resource_path
    from PySide6.QtGui import QIcon
    icon = resource_path("assets/icon.png")
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    win = MainWindow()
    win.show()
    if source:
        win.load_source(source)
    return app.exec()


def main() -> int:
    from core.version import __version__

    # macOS passa "-psn_..." ao abrir pelo Finder em versões antigas: ignorar
    sys.argv = [a for a in sys.argv if not a.startswith("-psn_")]
    p = argparse.ArgumentParser(description="VRI GerberQuote — análise de Gerber/Drill para orçamento")
    p.add_argument("--version", action="version", version=f"VRI GerberQuote {__version__}")
    p.add_argument("source", nargs="?", help="Arquivo .zip ou pasta do projeto")
    p.add_argument("--pdf", help="Gera o PDF técnico sem abrir a interface")
    p.add_argument("--pth-bot", action="store_true", help="PTH inserido pelo lado BOT")
    p.add_argument("--no-pth-top", action="store_true", help="PTH NÃO inserido pelo lado TOP")
    p.add_argument("--layers", type=int, help="Sobrescreve o número de layers")
    p.add_argument("--height", type=float, help="Sobrescreve a altura estimada de montagem (mm)")
    p.add_argument("--lote", type=int, default=100, help="Tamanho do lote para o tempo total (padrão 100)")
    args = p.parse_args()
    if args.pdf:
        if not args.source:
            p.error("--pdf exige o arquivo .zip ou a pasta do projeto")
        return run_cli(args)
    return run_gui(args.source)


if __name__ == "__main__":
    sys.exit(main())
