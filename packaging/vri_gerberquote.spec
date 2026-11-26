# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec do VRI GerberQuote (Windows, macOS e Linux).
# Uso (na raiz do projeto):  pyinstaller packaging/vri_gerberquote.spec --noconfirm
import re
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "core" / "version.py").read_text()).group(1)
APP_NAME = "VRI GerberQuote"

# Módulos Qt que o app não usa (reduz o tamanho do instalador)
EXCLUDES = [
    "tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtQuick3D",
    "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtBluetooth",
    "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors", "PySide6.QtSerialPort",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtDesigner",
    "pytest", "IPython", "matplotlib.tests", "numpy.tests", "scipy", "pandas", "lxml", "gi",
    "PyQt5", "PyQt6", "sympy", "notebook", "jupyter",
]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "assets"), "assets")],
    hiddenimports=["matplotlib.backends.backend_agg", "render.package_renderer"],
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)

icon = str(ROOT / "assets" / ("icon.icns" if sys.platform == "darwin" else "icon.ico"))

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VRI_GerberQuote",
    console=False,          # aplicativo de janela (sem prompt de comando)
    icon=icon,
    upx=False,
    target_arch=None,
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="VRI_GerberQuote")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=icon,
        bundle_identifier="br.com.vri.gerberquote",
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
            "NSHumanReadableCopyright": "VRI Indústria Eletrônica S.A.",
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "Projeto Gerber (ZIP)",
                "CFBundleTypeRole": "Viewer",
                "LSItemContentTypes": ["public.zip-archive"],
            }],
        },
    )
