; Instalador Windows do VRI GerberQuote (Inno Setup 6 - https://jrsoftware.org/isinfo.php)
; Gerado por packaging/windows/build_windows.bat ou pelo GitHub Actions.
; A versão vem da linha de comando:  ISCC /DAppVersion=1.1.0 VRI_GerberQuote.iss

#ifndef AppVersion
  #define AppVersion "1.2.1"
#endif
#define AppName "VRI GerberQuote"
#define AppPublisher "VRI Indústria Eletrônica S.A."
#define AppExe "VRI_GerberQuote.exe"

[Setup]
AppId={{6E0B3F7A-2C4D-4C8E-9C61-5B1E0A9D7F21}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\VRI\GerberQuote
DefaultGroupName=VRI GerberQuote
DisableProgramGroupPage=yes
OutputDir=..\..\dist\installer
OutputBaseFilename=VRI_GerberQuote_Setup_{#AppVersion}_Windows_x64
SetupIconFile=..\..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
; Permite instalar sem administrador (pasta do usuário) ou para todos (Arquivos de Programas)
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na Área de Trabalho"; GroupDescription: "Atalhos:"

[Files]
Source: "..\..\dist\VRI_GerberQuote\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Desinstalar {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Abrir o {#AppName}"; Flags: nowait postinstall skipifsilent
