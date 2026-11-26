# Instaladores Windows e macOS — VRI GerberQuote

O usuário final **não precisa de Python, VS Code nem de qualquer outro programa**: o instalador
leva junto o Python, o Qt (interface), as bibliotecas de desenho/PDF e o logotipo.

| Sistema | Arquivo gerado | Requisito no PC do usuário |
|---|---|---|
| Windows 10 (1809+) / 11, 64 bits | `VRI_GerberQuote_Setup_<versão>_Windows_x64.exe` | nenhum |
| macOS 12+ Apple Silicon (M1–M4) | `VRI_GerberQuote_<versão>_macOS_arm64.dmg` | nenhum |
| macOS 12+ Intel | `VRI_GerberQuote_<versão>_macOS_x86_64.dmg` | nenhum |

> Um instalador Windows só pode ser compilado em Windows, e um app macOS só em Mac
> (limitação do PyInstaller). Por isso há duas formas de gerar os instaladores:

## Opção A — Automática pelo GitHub (recomendada, não precisa de Mac nem de PC dedicado)

1. Crie um repositório (pode ser **privado**) no GitHub e envie esta pasta para ele.
2. Abra a aba **Actions** → **Build instaladores** → **Run workflow**.
3. Em ~15 minutos, os três instaladores aparecem em **Artifacts** no fim da execução.
4. Para publicar uma versão: altere `core/version.py`, crie a tag (ex.: `v1.1.0`) e envie-a;
   os instaladores ficam anexados automaticamente a uma *Release*.

O workflow (`.github/workflows/build-installers.yml`) roda os testes, gera os executáveis,
**abre cada executável e gera um PDF de teste** antes de empacotar.

## Opção B — Manual

**Windows** (em um PC com Python 3.11+ 64 bits e [Inno Setup 6](https://jrsoftware.org/isdl.php)):

```bat
packaging\windows\build_windows.bat
```

**macOS** (em um Mac com Python 3.11+; gera para a arquitetura daquele Mac):

```bash
bash packaging/macos/build_macos.sh
```

Resultado em `dist/installer/`.

## Instalação no PC do usuário

**Windows:** executar o `Setup.exe` → Avançar → Instalar. Cria atalho no Menu Iniciar (e na
Área de Trabalho, se marcado) e aparece em *Aplicativos instalados* para desinstalar. Não exige
administrador (instala na pasta do usuário; com administrador, oferece *Arquivos de Programas*).

**macOS:** abrir o `.dmg` e arrastar **VRI GerberQuote** para **Aplicativos**.

## Avisos de segurança na primeira execução (sem certificado digital)

Sem assinatura digital paga, os sistemas mostram um alerta na **primeira** abertura:

- **Windows (SmartScreen):** "O Windows protegeu o computador" → *Mais informações* → *Executar assim mesmo*.
- **macOS (Gatekeeper):** clique com o botão direito no app → *Abrir* → *Abrir*. No macOS 15+:
  *Ajustes do Sistema → Privacidade e Segurança → Abrir Mesmo Assim*.

Para eliminar os alertas (recomendado na distribuição para toda a empresa):

- **Windows:** certificado de assinatura de código (OV/EV) e `signtool sign` no `.exe` e no Setup.
- **macOS:** conta Apple Developer (US$ 99/ano). Defina `MACOS_SIGN_IDENTITY` e
  `MACOS_NOTARY_PROFILE` antes do `build_macos.sh`; o script assina, notariza e grampeia o DMG.

## Modo linha de comando (também no app instalado)

```bat
"C:\...\VRI GerberQuote\VRI_GerberQuote.exe" projeto.zip --pdf saida.pdf
```
