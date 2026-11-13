"""Consolidação: lei base (extraída do quadro oficial) + alterações em ordem de data.

Cada cidade tem um arquivo em cidades/<slug>.json:
  normas      -> leis base e alteradoras (com link oficial vindo do iaJus)
  quadros     -> anexos de parâmetros a extrair (url, leitor, norma, data)
  alteracoes  -> mudanças pontuais de valor feitas por leis posteriores
  manuais     -> valores digitados de leis sem quadro legível (sempre conferido=0 até revisão)
"""

import json
from pathlib import Path

from . import base, extrator, fontes

PASTA_CIDADES = Path(__file__).resolve().parent.parent / "cidades"


def carregar_cidade(slug):
    caminho = PASTA_CIDADES / f"{slug}.json"
    if not caminho.exists():
        raise SystemExit(f"Cidade sem configuração: {caminho}")
    return json.loads(caminho.read_text(encoding="utf-8"))


def cidades_disponiveis():
    return sorted(p.stem for p in PASTA_CIDADES.glob("*.json"))


def mapa_local(pasta, slug):
    """Arquivos baixados à mão (ex.: PDF que o site só entrega pelo navegador):
    <pasta>/<slug>/<nome do arquivo da URL> substitui o download."""
    if not pasta:
        return {}
    cfg = carregar_cidade(slug)
    out = {}
    for q in cfg.get("quadros", []):
        p = Path(pasta) / slug / Path(q["url"].split("?")[0]).name
        if p.exists():
            out[q["url"]] = str(p)
    return out


def atualizar(con, slug, *, usar_modelo=False, origem_local=None, fontes_locais=None, log=print):
    """Reconstrói a camada vigente de uma cidade. origem_local: {url: caminho} para rodar offline."""
    cfg = carregar_cidade(slug)
    cidade = cfg["cidade"]
    origem_local = {**mapa_local(fontes_locais, slug), **(origem_local or {})}
    resumo = {"novo": 0, "alterado": 0, "igual": 0, "mais_antiga": 0, "erros": []}

    for n in cfg.get("normas", []):
        base.gravar_norma(con, n, cidade)

    # 1) quadros da lei base, em ordem de data
    for q in sorted(cfg.get("quadros", []), key=lambda q: q["vigente_desde"]):
        leitor = "modelo" if (usar_modelo and q.get("leitor") == "modelo") else q.get("leitor")
        if leitor == "modelo" and not usar_modelo:
            resumo["erros"].append(f"{q['rotulo']}: precisa de --modelo (leiaute sem leitor por regra)")
            continue
        try:
            texto = fontes.texto_de(origem_local.get(q["url"], q["url"]))
            linhas, origem = extrator.extrair(texto, leitor)
        except Exception as e:
            resumo["erros"].append(f"{q['rotulo']}: {e}")
            log(f"  ! {q['rotulo']}: {e}")
            continue
        for l in linhas:
            r = base.gravar_parametro(
                con, cidade, l["zona"], l["parametro"], l["valor"], nota=l.get("nota"),
                norma_id=q["norma"], dispositivo=l.get("dispositivo") or q["rotulo"],
                fonte=q["url"], vigente_desde=q["vigente_desde"], origem=origem,
                conferido=1 if (origem == "extrator" and q.get("conferido")) else 0)
            resumo[r] += 1
        log(f"  {q['rotulo']}: {len(linhas)} valores ({origem})")

    # 2) valores manuais (leis sem quadro legível por máquina)
    for m in cfg.get("manuais", []):
        r = base.gravar_parametro(con, cidade, m["zona"], m["parametro"], m.get("valor"),
                                  nota=m.get("nota"), norma_id=m["norma"],
                                  dispositivo=m.get("dispositivo"), fonte=m.get("fonte"),
                                  vigente_desde=m["vigente_desde"], origem="manual",
                                  conferido=int(m.get("conferido", 0)))
        resumo[r] += 1

    # 3) alterações posteriores, em ordem de data
    for a in sorted(cfg.get("alteracoes", []), key=lambda a: a["vigente_desde"]):
        zonas = a["zonas"] if "zonas" in a else [a["zona"]]
        for z in zonas:
            r = base.gravar_parametro(con, cidade, z, a["parametro"], a.get("valor"),
                                      nota=a.get("nota"), norma_id=a["norma"],
                                      dispositivo=a.get("dispositivo"), fonte=a.get("fonte"),
                                      vigente_desde=a["vigente_desde"], origem="alteracao",
                                      conferido=int(a.get("conferido", 0)))
            resumo[r] += 1
    # 4) regras textuais que mudam a aplicação dos parâmetros (não são valores do quadro)
    for rg in cfg.get("regras", []):
        base.gravar_regra(con, cidade, rg)
    con.commit()
    return resumo


def pendencias(slug):
    """O que a configuração da cidade declara que ainda falta."""
    return carregar_cidade(slug).get("pendencias", [])
