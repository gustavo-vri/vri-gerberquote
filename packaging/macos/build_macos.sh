#!/usr/bin/env bash
# ==========================================================================
#  Gera o aplicativo macOS (.app) e a imagem de instalação (.dmg)
#  Requisito SOMENTE no Mac de build: Python 3.11+ (python.org ou Homebrew).
#  O app gerado é para a arquitetura do Mac onde roda o build:
#    Apple Silicon (M1/M2/M3/M4) -> arm64   |   Mac Intel -> x86_64
#  Uso:  bash packaging/macos/build_macos.sh
#  Resultado: dist/installer/VRI_GerberQuote_<versao>_macOS_<arch>.dmg
#
#  Assinatura/notarização (opcional, recomendada para distribuir sem alertas):
#    export MACOS_SIGN_IDENTITY="Developer ID Application: VRI ... (TEAMID)"
#    export MACOS_NOTARY_PROFILE="perfil-criado-com-notarytool-store-credentials"
# ==========================================================================
set -euo pipefail
cd "$(dirname "$0")/../.."

PY=${PYTHON:-python3}
VER=$(sed -n 's/^__version__ *= *"\(.*\)"/\1/p' core/version.py)
ARCH=$(uname -m)
APP="dist/VRI GerberQuote.app"
DMG="dist/installer/VRI_GerberQuote_${VER}_macOS_${ARCH}.dmg"

[ -d .venv-build ] || "$PY" -m venv .venv-build
source .venv-build/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt -r requirements-build.txt

echo "=== Testes ==="
QT_QPA_PLATFORM=offscreen python -m pytest -q

echo "=== PyInstaller ==="
pyinstaller packaging/vri_gerberquote.spec --noconfirm --clean

if [ -n "${MACOS_SIGN_IDENTITY:-}" ]; then
  echo "=== Assinando com: $MACOS_SIGN_IDENTITY ==="
  codesign --deep --force --options runtime --timestamp --sign "$MACOS_SIGN_IDENTITY" "$APP"
else
  # assinatura ad-hoc: necessária para rodar em Apple Silicon
  codesign --deep --force --sign - "$APP"
fi

echo "=== Criando DMG ==="
mkdir -p dist/installer dist/dmg
rm -rf dist/dmg/* "$DMG"
cp -R "$APP" dist/dmg/
ln -s /Applications dist/dmg/Aplicativos
hdiutil create -volname "VRI GerberQuote ${VER}" -srcfolder dist/dmg -ov -format UDZO "$DMG"

if [ -n "${MACOS_SIGN_IDENTITY:-}" ] && [ -n "${MACOS_NOTARY_PROFILE:-}" ]; then
  echo "=== Notarizando ==="
  codesign --force --sign "$MACOS_SIGN_IDENTITY" "$DMG"
  xcrun notarytool submit "$DMG" --keychain-profile "$MACOS_NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG"
fi

echo "Pronto: $DMG"
