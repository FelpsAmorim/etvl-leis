"""evtl-leis: buscador e consolidador de parâmetros urbanísticos.

Comandos:
  python -m evtl_leis diagnostico                  # o que este host alcança (rodar primeiro)
  python -m evtl_leis atualizar --cidade sao-paulo # baixa quadros, aplica alterações, grava a base
  python -m evtl_leis atualizar --todas [--modelo] # todas as cidades; --modelo libera o Claude Haiku
  python -m evtl_leis consultar --cidade sao-paulo --zona ZEM [--json]
  python -m evtl_leis historico --cidade sao-paulo --zona ZEM --parametro ca_max
  python -m evtl_leis vigiar --desde 2024-01-01    # Querido Diário: menções novas às leis base
  python -m evtl_leis avisos                       # avisos ainda não vistos
  python -m evtl_leis pendencias                   # o que falta por cidade
  python -m evtl_leis ler <url>                    # lê uma lei (HTML/PDF) e lista artigos e notas
  python -m evtl_leis zona --cidade sao-paulo --sql 009.096.0000   # ou --endereco "Av Paulista" --numero 1578, ou --lat/--lon
  python -m evtl_leis exportar                     # gera cloudflare/d1_dados.sql e cloudflare/src/fontes.json
"""

import argparse
import json
import sys

from . import base, consolidar, diagnostico, exportar, fontes, lote, vigia

ROTULOS = {"ca_min": "CA mínimo", "ca_bas": "CA básico", "ca_max": "CA máximo",
           "to_ate_500": "TO (lote até 500 m²)", "to_acima_500": "TO (lote acima de 500 m²)",
           "gabarito_m": "Gabarito (m)", "recuo_frente": "Recuo de frente (m)",
           "recuo_fundo_lat_ate_10m": "Recuo fundos/lat. até 10 m de altura",
           "recuo_fundo_lat_acima_10m": "Recuo fundos/lat. acima de 10 m de altura",
           "cota_parte_m2": "Cota parte (m²/unidade)",
           "permeabilidade_ate_500": "Permeabilidade mín. (até 500 m²)",
           "permeabilidade_acima_500": "Permeabilidade mín. (acima de 500 m²)"}


def _cidade_nome(slug):
    return consolidar.carregar_cidade(slug)["cidade"]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="evtl_leis", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="evtl_leis.db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("diagnostico")
    a = sub.add_parser("atualizar")
    g = a.add_mutually_exclusive_group(required=True)
    g.add_argument("--cidade")
    g.add_argument("--todas", action="store_true")
    a.add_argument("--modelo", action="store_true", help="usa o Claude Haiku 5.5 em quadros sem leitor por regra")
    a.add_argument("--fontes-locais", default="fontes_locais", help="pasta com PDFs baixados à mão (<pasta>/<cidade>/<arquivo>)")
    c = sub.add_parser("consultar")
    c.add_argument("--cidade", required=True)
    c.add_argument("--zona")
    c.add_argument("--json", action="store_true")
    h = sub.add_parser("historico")
    h.add_argument("--cidade", required=True)
    h.add_argument("--zona", required=True)
    h.add_argument("--parametro", required=True)
    v = sub.add_parser("vigiar")
    v.add_argument("--desde", required=True)
    v.add_argument("--cidade")
    sub.add_parser("avisos")
    sub.add_parser("pendencias")
    e = sub.add_parser("exportar")
    e.add_argument("--pasta", default=str(consolidar.PASTA_CIDADES.parent / "cloudflare"))
    z = sub.add_parser("zona", help="zona do lote pelo mapa vigente (hoje só São Paulo)")
    z.add_argument("--cidade", default="sao-paulo", choices=["sao-paulo"])
    z.add_argument("--sql", help="setor.quadra.lote-dígito")
    z.add_argument("--endereco")
    z.add_argument("--numero")
    z.add_argument("--lat", type=float)
    z.add_argument("--lon", type=float)
    z.add_argument("--json", action="store_true")
    l = sub.add_parser("ler")
    l.add_argument("url")
    args = ap.parse_args(argv)

    if args.cmd == "diagnostico":
        res = diagnostico.rodar()
        bloq = [r for r in res if not r[2].startswith("ok")]
        print(f"\n{len(res) - len(bloq)} de {len(res)} fontes alcançáveis.")
        return 1 if bloq else 0

    if args.cmd == "ler":
        dados, tipo = fontes.baixar(args.url)
        if tipo == "pdf":
            print(fontes.pdf_para_texto(dados))
            return 0
        artigos, links = fontes.html_para_artigos(dados)
        print(json.dumps({"artigos": artigos, "anexos": fontes.anexos_na_pagina(links)},
                         ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "zona":
        if not (args.sql or args.endereco or (args.lat is not None and args.lon is not None)):
            ap.error("informe --sql, --endereco ou --lat e --lon")
        r = lote.zona(sql=args.sql, endereco=args.endereco, numero=args.numero, lat=args.lat, lon=args.lon)
        if args.json:
            print(json.dumps(r, ensure_ascii=False, indent=2))
            return 0
        l = r.get("lote")
        if l:
            print(f"Lote {l['sql']} (condomínio {l['condominio']}, {l['tipo']}) {l['endereco'] or ''}"
                  f" — terreno {l['area_terreno_m2'] or '?'} m²")
            if r.get("outros_lotes"):
                print(f"  ({r['outros_lotes']} outro(s) lote(s) com o mesmo filtro; usei o primeiro fiscal)")
        else:
            print(r.get("aviso", ""))
        for v in ("2024", "2016"):
            zs = ", ".join(f"{z['zona']} ({z['fracao']:.0%})" for z in r[f"zonas_{v}"]) or "nenhuma"
            print(f"Zona {v}: {zs}   [{r[f'fonte_{v}']['descricao']}; {r[f'fonte_{v}']['camada']}]")
        if r.get("mudou_desde_2016"):
            print("ATENÇÃO: a zona mudou em relação ao mapa de 2016.")
        if r["zona"]:
            print(f"Parâmetros: python -m evtl_leis consultar --cidade sao-paulo --zona {r['zona']}")
        return 0

    if args.cmd == "pendencias":
        for slug in consolidar.cidades_disponiveis():
            print(f"\n{_cidade_nome(slug)}:")
            for p in consolidar.pendencias(slug):
                print(f"  - {p}")
        return 0

    con = base.abrir(args.db)

    if args.cmd == "atualizar":
        slugs = consolidar.cidades_disponiveis() if args.todas else [args.cidade]
        erros = 0
        for s in slugs:
            print(f"{_cidade_nome(s)}:")
            r = consolidar.atualizar(con, s, usar_modelo=args.modelo, fontes_locais=args.fontes_locais)
            print(f"  novos {r['novo']}, alterados {r['alterado']}, iguais {r['igual']}, "
                  f"alterações antigas ignoradas {r['mais_antiga']}")
            for e in r["erros"]:
                print(f"  ! {e}")
            erros += len(r["erros"])
        return 1 if erros else 0

    if args.cmd == "consultar":
        linhas = base.vigente(con, _cidade_nome(args.cidade), args.zona)
        if args.json:
            print(json.dumps(linhas, ensure_ascii=False, indent=2))
            return 0
        zona_atual = None
        for r in linhas:
            if r["zona"] != zona_atual:
                zona_atual = r["zona"]
                print(f"\n{zona_atual}")
            val = "—" if r["valor"] is None else f"{r['valor']:g}".replace(".", ",")
            conf = "" if r["conferido"] else "  [conferir]"
            print(f"  {ROTULOS.get(r['parametro'], r['parametro']):<45} {val:>6}   "
                  f"{r['norma_id']} {r['dispositivo'] or ''} desde {r['vigente_desde']}{conf}")
            if r["nota"]:
                print(f"      nota: {r['nota']}")
        if not linhas:
            print("Nada na base para esse filtro. Rode 'atualizar' antes.")
        rg = base.regras(con, _cidade_nome(args.cidade), args.zona)
        if rg:
            print("\nRegras que mudam a aplicação desses parâmetros:")
            for r in rg:
                print(f"  - {r['texto']} ({r['norma_id']}, {r['dispositivo']})")
        return 0

    if args.cmd == "historico":
        for r in base.historico(con, _cidade_nome(args.cidade), args.zona, args.parametro):
            print(f"{r['vigente_desde']} → {r['vigente_ate'] or 'hoje'}: {r['valor']}  "
                  f"({r['norma_id']} {r['dispositivo'] or ''}, {r['origem']})")
        return 0

    if args.cmd == "vigiar":
        slugs = [args.cidade] if args.cidade else consolidar.cidades_disponiveis()
        total = sum(vigia.vigiar(con, s, args.desde) for s in slugs)
        print(f"{total} avisos novos. Veja com: python -m evtl_leis avisos")
        return 0

    if args.cmd == "exportar":
        t = exportar.exportar(con, args.pasta)
        print(f"Gravado {args.pasta}/d1_dados.sql ({t}) e src/fontes.json")
        return 0

    if args.cmd == "avisos":
        for r in con.execute("SELECT * FROM avisos WHERE visto=0 ORDER BY data DESC"):
            print(f"[{r['data']}] {r['cidade']} {r['norma_id']}: {r['url']}\n    {r['trecho'][:300]}\n")
        return 0


if __name__ == "__main__":
    sys.exit(main())
