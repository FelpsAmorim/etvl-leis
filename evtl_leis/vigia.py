"""Vigia: procura no Querido Diário (diários oficiais municipais) menções às leis base
publicadas depois da última checagem. Cada menção nova vira um aviso na base para
alguém conferir se é alteração (e, se for, entrar em alteracoes do JSON da cidade).

O iaJus também sabe listar normas que alteram outras (ex.: Lei 2.402/2019 "ALTERA a
Lei 1.838/2014" em Manaus), mas é acessado pelo conector MCP dentro do Claude, não
por API aberta. Por isso a rotina automática usa o Querido Diário e a checagem pelo
iaJus fica como passo do Claude (ver README).
"""

import json
import urllib.parse
import urllib.request

from . import base, consolidar, fontes

QD = "https://api.queridodiario.ok.org.br/gazettes"


def _consulta(ibge, termo, desde, limite=20):
    q = urllib.parse.urlencode({"territory_ids": ibge, "querystring": f'"{termo}"',
                                "published_since": desde, "size": limite,
                                "excerpt_size": 400, "number_of_excerpts": 2,
                                "sort_by": "descending_date"})
    req = urllib.request.Request(f"{QD}?{q}", headers=fontes.UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def termos_da_norma(n):
    numero = str(n["numero"])
    com_ponto = f"{int(numero):,}".replace(",", ".") if numero.isdigit() else numero
    return sorted({f"Lei {com_ponto}", f"Lei nº {com_ponto}", f"Lei n° {com_ponto}"})


def vigiar(con, slug, desde, consulta=_consulta, log=print):
    cfg = consolidar.carregar_cidade(slug)
    novos = 0
    for n in cfg.get("normas", []):
        if n.get("papel") not in ("base", "plano_diretor", "zoneamento"):
            continue
        for termo in termos_da_norma(n)[:1]:  # um termo por norma basta para a busca entre aspas
            try:
                res = consulta(cfg["ibge"], termo, desde)
            except Exception as e:
                log(f"  ! Querido Diário indisponível para {termo}: {e}")
                continue
            for g in res.get("gazettes", []):
                trecho = " … ".join(g.get("excerpts", []))[:800]
                if base.gravar_aviso(con, cfg["cidade"], n["id"], g.get("date"), "querido_diario",
                                     g.get("url") or g.get("txt_url"), trecho):
                    novos += 1
    con.commit()
    return novos
