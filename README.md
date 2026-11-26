# VRI GerberQuote

Aplicativo desktop (Python + PySide6) da **VRI Indústria Eletrônica S.A.** para analisar
arquivos Gerber e Drill (Excellon) de clientes e gerar, para o setor comercial:

- número de layers, dimensões X/Y e área da PCI;
- **contagem de COMPONENTES** SMD (Top/Bot) e PTH — pelo Pick & Place do cliente (exata) ou por
  estimativa geométrica (pads + furos + serigrafia) — além de pads e furos;
- **tempos de montagem**: SMD (impressão + Fuji AIM + forno de refusão) e PTH manual (IPC-A-610 /
  J-STD-001), por placa e por lote, com parâmetros editáveis;
- tabela de furação no padrão **Drill Table do Altium**;
- status de montagem por lado, com os avisos padronizados;
- altura estimada de montagem e **embalagem sugerida (X × Y × Z)**;
- desenhos técnicos (Vista Geral, Montagem TOP, Montagem BOT espelhada, **Dimensional e
  Furação** num único desenho, **Embalagem**) e o **PDF Técnico Consolidado**;
- **desenho de embalagem avulso** (PDF A4 paisagem) para enviar ao fabricante de caixas cotar.

**Instaladores Windows/macOS (sem Python no PC do usuário): veja [INSTALADORES.md](INSTALADORES.md).**

## Instalação

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    |    Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

Coloque o logotipo oficial em `assets/logo_vri.png` (o arquivo incluído é um placeholder).

## Uso

```bash
python main.py                         # interface gráfica
python main.py projeto.zip             # abre já carregando um projeto
python main.py projeto.zip --pdf saida.pdf [--pth-bot] [--no-pth-top] [--layers N] [--height Z]
```

Na interface: importe um `.zip` ou uma pasta (ou arraste para a janela), confira o tipo de cada
arquivo nos dropdowns, marque o lado de inserção PTH e exporte o PDF. Os campos *Número de Layers*
e *Altura Est. Montagem* são preenchidos automaticamente; ao editá-los ficam amarelos (manual)
e o botão **↺ auto** volta ao valor calculado.

Para gerar projetos de exemplo: `python -m tests.synthetic` (cria a pasta `exemplos/`).

## Estrutura

| Módulo | Responsabilidade |
|---|---|
| `core/extractor.py` | Importa `.zip` (inclusive aninhados, com proteção zip-slip) ou pasta; varredura recursiva; descarta PDF, IPC, relatórios `.txt` não-Excellon etc. pelo conteúdo |
| `core/classifier.py` | Classifica camadas por extensão Altium, atributo Gerber X2 e palavras-chave (KiCad, Eagle, OrCAD…); separa Drill PTH/NPTH/misto; conta layers |
| `core/parsers.py` | Parsers próprios de Gerber RS-274X/X2 (aberturas, macros AM, arcos, regiões) e Excellon (LZ/TZ, `FILE_FORMAT`, INCH/METRIC, plating por ferramenta) |
| `core/analyzer.py` | Regras de negócio: contagens, status de montagem, altura e embalagem |
| `core/geometry.py` | Reconstrói o polígono do contorno, detecta o formato e define as cotas |
| `core/pnp.py` | Leitura de Pick & Place (Altium, KiCad, Eagle, CSV) |
| `core/components.py` | Contagem de componentes SMD/PTH (Pick & Place ou estimativa geométrica + serigrafia) |
| `core/timing.py` | Tempos SMD (Fuji AIM + refusão) e PTH manual IPC; parâmetros em `~/.vri_gerberquote/tempos.json` |
| `core/packaging.py` | Especificação da caixa (FEFCO, material, quantidade, medidas internas, planificação) |
| `render/gerber_renderer.py` | Desenhos da placa (Matplotlib/Agg) usados na tela e no PDF |
| `render/package_renderer.py` | Desenho de embalagem (isométrico, vista superior, corte, planificação, especificação) |
| `export/pdf_generator.py` | PDF técnico consolidado e PDF avulso de embalagem (ReportLab) |
| `packaging/` + `.github/workflows/` | Build dos instaladores Windows (Inno Setup) e macOS (DMG) |
| `ui/main_window.py` | Interface PySide6 (processamento em segundo plano) |
| `tests/` | Gerador de projetos sintéticos Altium + suíte `pytest` |

## Regras implementadas

**Montagem** — SMD é identificado pelos pads da camada Paste (flashes + regiões); PTH pelos furos
metalizados do Drill, aplicados aos lados marcados nos checkboxes.

| Situação do lado | Desenho | Aviso |
|---|---|---|
| Sem SMD e sem PTH | **omitido** | `LADO X: SEM MONTAGEM` |
| Só SMD | gerado | `LADO X: SEM MONTAGEM PTH (APENAS SMD)` |
| Só PTH | gerado | `LADO X: SEM MONTAGEM SMD (APENAS PTH)` |
| SMD + PTH | gerado | `LADO X: MONTAGEM SMD + PTH` |

**Cotas (Dimensional e Furação)** — placa retangular ou quadrada (inclusive com cantos
arredondados): cotas pelos lados X e Y. Placa em L ou outro formato: cotas pelos **lados de maior
comprimento** (maior lado horizontal, maior lado vertical e maior lado inclinado, se for o maior de
todos). Se o maior lado reto não alcança o envelope (formato T, trapézio, placa redonda), a cota
**total máxima** é acrescentada numa segunda linha, pois é ela que dimensiona a embalagem. O mesmo
desenho traz os furos por broca (PTH, via, NPTH) e a tabela de brocas.

**Embalagem** — medidas **internas** C × L × A (C sempre a maior), arredondadas para cima.
Modelos: FEFCO 0201 (caixa normal, com planificação de referência) ou FEFCO 0427 (maleta corte e
vinco; faca a cargo do fabricante). Material e quantidade selecionáveis na tela.

**Componentes (não pads)** — um resistor tem 2 pads e é 1 componente. Com o arquivo **Pick & Place**
no pacote (Altium `Pick Place for <proj>.txt`, KiCad `.pos`/`-pos.csv`, Eagle `.mnt/.mnb`) a contagem
é exata por designador (fiduciais e furos de fixação são excluídos). Sem ele, os pads de Paste e os
furos PTH são agrupados por padrão de encapsulamento (pares de passivos, fileiras de CI, SOIC/QFP/QFN
com pad térmico, SOT-23, BGA, DIP, barras de pinos, axiais/radiais, bornes) e os contornos fechados da
serigrafia unem grupos do mesmo corpo. Para PTH, o **contorno do componente na serigrafia** é a
referência principal: furos dentro do mesmo contorno fechado formam 1 componente (conectores
empilhados ou encostados, radiais lado a lado e cristais ficam separados corretamente); furos dentro
do contorno de um SMD ou sem contorno e sem padrão de pinos são tratados como furos de passagem. As caixas tracejadas nas abas de montagem mostram cada
componente para conferência; os totais podem ser corrigidos manualmente no painel lateral.
Furos PTH isolados (fixação metalizada, pontos de teste) não contam como componente.

**Tempos** — SMD por lado: *takt* da linha = maior entre impressão de pasta, pick & place
(componentes × tempo efetivo por categoria + manuseio) e forno (comprimento da placa + espaço ÷
velocidade da esteira), + inspeção; setup por lote = programa + feeders (tipos de componente) +
primeira peça. PTH: componentes × (preparo + inserção + inspeção) + juntas × (solda manual + corte)
+ manuseio; setup por lote. Os valores padrão são referências iniciais: calibre com os tempos reais.

**Altura Z** — placa nua 10 mm · SMD 1 lado 15 mm · SMD 2 lados 20 mm · PTH 1 lado 25 mm ·
PTH + componentes no lado oposto 30 mm. **Embalagem** = (X + 20) × (Y + 20) × (Z + 10) mm.

### Decisões de projeto (ajustáveis)

- **Vias × PTH de componente**: furos metalizados com Ø ≤ 0,50 mm são tratados como vias e não
  caracterizam montagem PTH (senão toda placa SMD com vias seria "PTH"). O limiar é editável na
  tela (`Ø máx. de via`); use 0 para considerar todo furo metalizado como PTH.
- **Paste ausente**: seguindo a regra, sem Paste o SMD do lado não é contabilizado; o sistema
  avisa quando a máscara de solda indica aberturas fora de furos e o orçamentista pode
  reclassificar a máscara como Paste no dropdown.
- **Vários contornos** (ex.: `.GKO` + `.GM1` com cotas/legenda): usa o menor que envolve o cobre.
- **Sem contorno**: dimensões pelo envelope do cobre/furação, com alerta.
- **Drill Altium combinado** (`;TYPE=PLATED` e `;TYPE=NON_PLATED` no mesmo arquivo): plating por
  ferramenta.

## Testes

```bash
pytest -v
```

Cobre os 3 cenários obrigatórios (4L com `/Gerber` e `/Drill` + Bot vazio; 2L só SMD com NPTH;
1L só PTH), as regras de altura/embalagem, classificação de KiCad/X2/Eagle, parsers, PDF e um
teste de fumaça da interface (offscreen).
