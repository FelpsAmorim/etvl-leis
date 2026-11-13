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
    python -m evtl_leis historico --cidade sao-paulo --zona ZEM --parametro ca_max
    python -m evtl_leis vigiar --desde 2024-01-01    # menções novas às leis base nos diários oficiais
    python -m evtl_leis avisos
    python -m evtl_leis pendencias                   # o que falta, por cidade
    python -m evtl_leis ler <url>                    # artigos, notas de alteração e anexos de uma lei

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

## Estado por cidade (12/11/2026)

**São Paulo.** Quadro 3 (CA, TO, gabarito, recuos, cota parte) com leitor por regra, 7 zonas testadas. Lidos os arts. 1 a 41 da Lei 18.081/2024 e toda a 18.177/2024: nenhum muda número do Quadro 3. Sete mudanças de regra entraram em `regras`, e o Mapa 1 de 2024 mudou a zona de vários lotes. Faltam: o restante da 18.081, a Lei 18.209 citada nos laudos (não encontrada), a permeabilidade (Quadro 3A + Mapa 3), os usos (Quadro 4) e a zona pelo mapa de 2024.

**Natal.** Valores gerais da LC 208/2022 (CA básico 1,0; TO 80%; permeabilidade 20%; gabarito 140 m; recuos até 7 m), tirados do texto da lei. O CA máximo por bairro e o quadro de recuos estão no Anexo II, que não está publicado no SAPL.

**Manaus.** Só as normas cadastradas. O SAPL da Câmara bloqueia robôs, e não achei a Lei 1.838 nem o Anexo I da 2.402 em outra fonte aberta. Caminhos: baixar os PDFs à mão para `fontes_locais/manaus/`, ou usar os parâmetros por setor do ArcGIS da IMPLURB (CAMT e gabarito).

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

Consulta, vigia e diagnóstico rodam num Worker com D1; a extração dos PDFs roda no GitHub Actions ou no Mac e carrega o D1. Passo a passo em `cloudflare/DEPLOY.md`.
