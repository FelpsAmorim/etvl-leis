"""Exporta a base local para a Cloudflare: d1_dados.sql (carga no D1) e src/fontes.json
(lista de fontes para o /diagnostico e normas base para a vigia do Worker)."""

import json
from pathlib import Path

from . import consolidar, diagnostico, vigia

TABELAS = {"normas": None, "parametros": None, "regras": None}


def _lit(v):
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def exportar(con, pasta):
    pasta = Path(pasta)
    (pasta / "src").mkdir(parents=True, exist_ok=True)
    linhas = ["-- Gerado por: python -m evtl_leis exportar. Substitui normas, parametros e regras (avisos ficam)."]
    total = {}
    for t in TABELAS:
        linhas.append(f"DELETE FROM {t};")
        rows = con.execute(f"SELECT * FROM {t}").fetchall()
        total[t] = len(rows)
        for r in rows:
            cols = [k for k in r.keys() if k != "id" or t == "normas"]
            linhas.append(f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({', '.join(_lit(r[c]) for c in cols)});")
    (pasta / "d1_dados.sql").write_text("\n".join(linhas) + "\n", encoding="utf-8")

    cidades = {}
    for slug in consolidar.cidades_disponiveis():
        cfg = consolidar.carregar_cidade(slug)
        cidades[slug] = {
            "cidade": cfg["cidade"], "ibge": cfg["ibge"],
            "normas_base": [{"id": n["id"], "termo": vigia.termos_da_norma(n)[0]} for n in cfg["normas"]
                            if n.get("papel") in ("base", "plano_diretor", "zoneamento")],
        }
    (pasta / "src" / "fontes.json").write_text(
        json.dumps({"alvos": diagnostico.alvos(), "cidades": cidades}, ensure_ascii=False, indent=2), encoding="utf-8")
    return total
