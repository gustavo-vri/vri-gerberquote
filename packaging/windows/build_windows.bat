@echo off
REM ==========================================================================
REM  Gera o instalador Windows do VRI GerberQuote
REM  Requisitos SOMENTE na maquina de build (nao no PC do usuario final):
REM    - Python 3.11+ 64 bits (https://www.python.org)
REM    - Inno Setup 6 (https://jrsoftware.org/isdl.php)
REM  Uso: execute este arquivo a partir de qualquer pasta.
REM  Resultado: dist\installer\VRI_GerberQuote_Setup_<versao>_Windows_x64.exe
REM ==========================================================================
setlocal
cd /d "%~dp0\..\.."

if not exist .venv-build (
    python -m venv .venv-build || goto :erro
)
call .venv-build\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt -r requirements-build.txt || goto :erro

echo === Rodando testes ===
set QT_QPA_PLATFORM=offscreen
python -m pytest -q || goto :erro
set QT_QPA_PLATFORM=

echo === Gerando executavel (PyInstaller) ===
pyinstaller packaging\vri_gerberquote.spec --noconfirm --clean || goto :erro

for /f "tokens=2 delims==" %%v in ('findstr "__version__" core\version.py') do set VER=%%~v
set VER=%VER: =%
set VER=%VER:"=%

set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist %ISCC% set ISCC="%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist %ISCC% (
    echo Inno Setup 6 nao encontrado. Instale em https://jrsoftware.org/isdl.php
    goto :erro
)
echo === Gerando instalador (Inno Setup) versao %VER% ===
%ISCC% /DAppVersion=%VER% packaging\windows\VRI_GerberQuote.iss || goto :erro

echo.
echo Instalador pronto em dist\installer
exit /b 0

:erro
echo.
echo *** FALHA NO BUILD ***
exit /b 1
