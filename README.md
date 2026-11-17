# evtl-leis

Buscador e consolidador de parâmetros urbanísticos para EVTL.

- **iaJus** diz qual lei vale, se está vigente e onde está o texto oficial.
- **evtl-leis** baixa o texto/anexo oficial, extrai os parâmetros por zona, aplica as alterações posteriores em ordem de data e grava tudo numa base versionada, cada valor com a norma, o dispositivo e o link de onde saiu.

## Primeiro passo num host novo: diagnóstico

    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    python -m evtl_leis diagnostico

Mostra, fonte por fonte, o que a máquina alcança. No ambiente de nuvem onde isto foi escrito, só a API do Claude passou (1 de 15). Todos os sites de prefeitura, SAPL, GeoSampa, Querido Diário e iaJus deram 403 do proxy. É esse o teste para saber se o host novo resolve o problema de rede.

## Uso

    python -m evtl_leis atualizar --todas            # baixa quadros, aplica alterações, grava evtl_leis.db
    python -m evtl_leis consultar --cidade sao-paulo --zona ZEM
    python -m evtl_leis consultar --cidade sao-paulo --zona ZEM --json   # para o motor de cálculo
    python -m evtl_leis consultar --cidade sao-paulo --zona ZPI-2 --data 2020-01-01   # valores vigentes nessa data
    python -m evtl_leis historico --cidade sao-paulo --zona ZEM --parametro ca_max
    python -m evtl_leis vigiar --desde 2024-01-01    # menções novas às leis base nos diários oficiais
    python -m evtl_leis avisos
    python -m evtl_leis pendencias                   # o que falta, por cidade
    python -m evtl_leis ler <url>                    # artigos, notas de alteração e anexos de uma lei

### Busca do lote (lote -> zona -> parâmetros + regras)

    python -m evtl_leis buscar --cidade sao-paulo --sql 009.096.0000           # ou --endereco "R Samuel das Neves" --numero 74, ou --lat/--lon
    python -m evtl_leis buscar --cidade manaus --endereco "Av Djalma Batista, 1661"   # ou --cgl, ou --lat/--lon
    python -m evtl_leis buscar --cidade natal --bairro Tirol --bacia CS --eixo "Prudente de Morais"   # ou --lat/--lon

| Cidade | Entrada | Fonte da zona | O que o usuário ainda informa |
|---|---|---|---|
| São Paulo | SQL, endereço + número, lat/lon | GeoSampa: Mapa 1 da Lei 18.177/2024 (e o de 2016, para comparar) | nada |
| Manaus | endereço, CGL, lat/lon | ArcGIS da IMPLURB: setores, subsetores, lotes, bairros | nada |
| Natal | bairro/bacia/eixo, ou lat/lon | portal ArcGIS da SEMURB: bairro e áreas especiais (ZA, ZPAs, AEIS...) | bacia de esgotamento e eixo (Mapas 2/2A sem serviço aberto) |

Lote em mais de uma zona (SP): a busca estima a fração de cada zona por amostragem de pontos.

**Parâmetros aplicáveis.** A resposta traz `aplicaveis`: um valor por parâmetro, já resolvida a precedência, com a zona de onde saiu e o que ela substituiu.

| Cidade | Do geral para o específico (o último prevalece) |
|---|---|
| São Paulo | zona do Mapa 1 → camada sobreposta (hoje: `ZEM / Arco Tietê`, CA máx. 4,0 pela Res. CTLU 004/2018) |
| Manaus | setor → subsetor (o que o subsetor não define vem do setor, marcado `herdado`) |
| Natal | município → bairro (teto entre as bacias) → bacia → bairro × bacia → eixo |

Lote de SP em mais de uma zona: `aplicaveis` vem vazio (`null`) e a resposta avisa, porque não existe um valor só.

**Arco Tietê (SP).** A busca consulta a camada `pde_macroarea_lei_18209` do GeoSampa. Se ela não responder, a resposta avisa; `--arco-tiete sim|nao` (ou `arco_tiete=` na API) decide na mão.

`--data AAAA-MM-DD` (em `buscar` e `consultar`) devolve os parâmetros na versão que valia naquela data. Use a data do laudo ao comparar com os EVTLs do gabarito: um laudo de 2022 em SP seguiu a Lei 16.402 antes das mudanças de 2024. As regras textuais ainda não têm data de vigência; a resposta avisa.

Para leiaute de quadro sem leitor por regra: `atualizar --modelo` (precisa de `ANTHROPIC_API_KEY`; usa o Claude Haiku 5.5 só para transcrever, e as linhas saem marcadas "conferir").

Site que não deixa robô baixar: salve o PDF em `fontes_locais/<cidade>/<nome do arquivo da URL>` e rode `atualizar` de novo.

## Como funciona

    cidades/<cidade>.json      configuração: normas (links do iaJus), quadros, manuais, alterações, regras, pendências
    evtl_leis/fontes.py        download, PDF -> texto, HTML -> artigos (ignora texto riscado, lê "Redação dada pela Lei ...")
    evtl_leis/extrator.py      quadro -> linhas por zona (leitor por regra; Haiku para leiaute desconhecido)
    evtl_leis/consolidar.py    lei base + manuais + alterações em ordem de data + regras
    evtl_leis/base.py          SQLite: normas, parametros (vigente_desde/vigente_ate), regras, avisos
    evtl_leis/vigia.py         Querido Diário: avisa quando um diário oficial cita uma lei base
    evtl_leis/diagnostico.py   teste de alcance das fontes

Regras da base:
- Nada é apagado. Quando uma alteração muda um valor, a linha anterior ganha `vigente_ate` e entra uma nova. Dá para saber o valor de hoje e o de qualquer data passada.
- Todo valor tem `norma_id`, `dispositivo` e `fonte` (URL).
- `conferido = 0` até alguém (Danilo) validar. Valores do modelo e valores digitados nunca entram como conferidos.
- **Regras** são mudanças de texto que alteram como o parâmetro se aplica (ex.: gabarito contado a partir de 6 m do perfil natural), sem mudar o número do quadro. A consulta mostra as regras junto com os valores.

## Onde entra o iaJus

O iaJus é um conector MCP usado pelo Claude, não uma API aberta para o script. O fluxo é:
1. No Claude, com o conector iaJus: `localizar_norma_fonte_oficial` e `pesquisar_normas` (modo número) para cada lei base e para as leis que a alteram (a ementa diz "ALTERA a Lei ...").
2. O resultado (link oficial, data, vigência) entra em `cidades/<cidade>.json`, bloco `normas`.
3. O `vigiar` roda sozinho (cron semanal) pelo Querido Diário. Um aviso novo é conferido no iaJus e, se for alteração, vira entrada em `alteracoes` ou `regras`.

## Estado por cidade (13/11/2026)

Base: 3.187 parâmetros, 23 regras e 12 normas, todos com `conferido=0`.

**São Paulo.** Lei 16.402/2016: Quadro 3 (34 zonas), Quadro 3A (permeabilidade e Quota Ambiental) e Quadro 4 (usos, 40 zonas × 59 subcategorias). Lei 18.081/2024 lida inteira (art. 72 muda a TO de ZPI-2; art. 73 muda usos; o resto entrou como regras). Lei 18.177/2024: regras de aplicação e o Mapa 1 novo. Busca do lote pelo GeoSampa. Faltam: Mapa 3 (perímetro PA), linhas AVP-2/AC-1/AC-2 do Quadro 3, sobreposições (ZEPEC, operação urbana, tombamento), checar se a 18.177 mudou o Quadro 4 e a Lei 18.209.

Gabarito: os 5 EVTLs da Legali foram comparados com a base em 17/11/2026 (37 de 66 itens batem, 6 divergem, 17 faltam na base). As divergências de CA na ZEM do Arco Tietê e de setor × subsetor em Manaus foram corrigidas; as outras estão nas pendências.

**Natal.** LC 208/2022: valores gerais e Anexo II, Quadros 1.1 a 1.4 (30 bacias, 36 bairros, 108 combinações bairro × bacia, 100 valores de eixos). Busca do bairro e das áreas especiais pela coordenada. Faltam: Quadro 2 (recuos, imagem no PDF), a bacia do lote (sem mapa aberto) e confirmar a LC 261/2025 e a Lei 7.801.

**Manaus.** 116 valores de 18 setores e 16 subsetores, do ArcGIS da IMPLURB. Busca do lote por endereço, CGL ou coordenada. Faltam: TO e recuos (Lei 1.838/2014, SAPL bloqueia robôs) e confirmar que o gabarito está em pavimentos.

Veja `python -m evtl_leis pendencias` para a lista completa.

## Testes

    python -m unittest discover -s tests     # offline, sem rede

## Subir para o git e rodar no host

    git init && git add . && git commit -m "evtl-leis: buscador e consolidador"
    git remote add origin <repo> && git push -u origin main
    # no host:
    git clone <repo> && cd evtl-leis && pip install -r requirements.txt
    python -m evtl_leis diagnostico && python -m evtl_leis atualizar --todas
    # cron semanal: python -m evtl_leis vigiar --desde $(date -d '-8 days' +%F)

## Cloudflare (plano free)

Consulta, busca do lote, vigia e diagnóstico rodam num Worker com D1; a extração dos PDFs roda no GitHub Actions ou no Mac e carrega o D1. Passo a passo em `cloudflare/DEPLOY.md`.

Rotas: `/buscar` (mesmos parâmetros da CLI: `cidade`, `sql`, `endereco`, `numero`, `cgl`, `bairro`, `bacia`, `eixo`, `lat`, `lon`, `data`, `arco_tiete`), `/consultar`, `/historico`, `/normas`, `/avisos`, `/diagnostico`, `/vigiar`. Exemplo:

    /buscar?cidade=natal&bairro=Tirol&bacia=CS
    /buscar?cidade=sao-paulo&sql=009.096.0000&data=2022-05-01

A lógica da busca existe em dois lugares: `evtl_leis/busca.py` + `lote.py` (referência) e `cloudflare/src/busca.js`. Mudou um, mude o outro; os testes dos dois usam os mesmos casos.

    cd cloudflare && npm test    # testes do Worker, offline
