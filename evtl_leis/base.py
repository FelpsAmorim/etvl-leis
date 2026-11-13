"""Base versionada (SQLite): cada parâmetro com a norma, o dispositivo, a fonte e a vigência.

Regra: nunca apagar. Quando uma alteração muda um valor, a linha antiga recebe
vigente_ate e entra uma linha nova. Assim dá para responder "qual era o CA da ZEM
em 2020" e "qual é hoje".
"""

import sqlite3
from datetime import datetime, timezone

ESQUEMA = """
CREATE TABLE IF NOT EXISTS normas (
  id TEXT PRIMARY KEY, cidade TEXT, tipo TEXT, numero TEXT, ano INTEGER,
  data TEXT, ementa TEXT, link TEXT, papel TEXT, vigencia TEXT, atualizado_em TEXT);
CREATE TABLE IF NOT EXISTS parametros (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cidade TEXT NOT NULL, zona TEXT NOT NULL, parametro TEXT NOT NULL,
  valor REAL, nota TEXT,
  norma_id TEXT, dispositivo TEXT, fonte TEXT,
  vigente_desde TEXT, vigente_ate TEXT,
  origem TEXT CHECK (origem IN ('extrator','alteracao','manual','modelo')),
  conferido INTEGER DEFAULT 0, criado_em TEXT);
CREATE INDEX IF NOT EXISTS ix_param ON parametros(cidade, zona, parametro, vigente_ate);
CREATE TABLE IF NOT EXISTS regras (
  id INTEGER PRIMARY KEY AUTOINCREMENT, cidade TEXT, zonas TEXT, texto TEXT,
  norma_id TEXT, dispositivo TEXT, UNIQUE(cidade, norma_id, dispositivo));
CREATE TABLE IF NOT EXISTS avisos (
  id INTEGER PRIMARY KEY AUTOINCREMENT, cidade TEXT, norma_id TEXT, data TEXT,
  fonte TEXT, url TEXT, trecho TEXT, visto INTEGER DEFAULT 0, criado_em TEXT,
  UNIQUE(cidade, norma_id, url));
"""


def agora():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def abrir(caminho="evtl_leis.db"):
    con = sqlite3.connect(caminho)
    con.row_factory = sqlite3.Row
    con.executescript(ESQUEMA)
    return con


def gravar_norma(con, n, cidade):
    con.execute("""INSERT INTO normas (id, cidade, tipo, numero, ano, data, ementa, link, papel, vigencia, atualizado_em)
                   VALUES (:id,:cidade,:tipo,:numero,:ano,:data,:ementa,:link,:papel,:vigencia,:atualizado_em)
                   ON CONFLICT(id) DO UPDATE SET ementa=excluded.ementa, link=excluded.link,
                     vigencia=excluded.vigencia, atualizado_em=excluded.atualizado_em""",
                {"id": n["id"], "cidade": cidade, "tipo": n.get("tipo"), "numero": n.get("numero"),
                 "ano": n.get("ano"), "data": n.get("data"), "ementa": n.get("ementa"),
                 "link": n.get("link"), "papel": n.get("papel"), "vigencia": n.get("vigencia"),
                 "atualizado_em": agora()})


def vigente(con, cidade, zona=None, parametro=None):
    sql = "SELECT * FROM parametros WHERE cidade=? AND vigente_ate IS NULL"
    args = [cidade]
    if zona:
        sql += " AND zona=?"
        args.append(zona)
    if parametro:
        sql += " AND parametro=?"
        args.append(parametro)
    return [dict(r) for r in con.execute(sql + " ORDER BY zona, parametro", args)]


def gravar_parametro(con, cidade, zona, parametro, valor, *, norma_id, dispositivo, fonte,
                     vigente_desde, origem, nota=None, conferido=0):
    """Insere a versão nova e fecha a anterior. Não duplica se nada mudou."""
    atual = con.execute("""SELECT * FROM parametros WHERE cidade=? AND zona=? AND parametro=?
                           AND vigente_ate IS NULL""", (cidade, zona, parametro)).fetchone()
    if atual and atual["valor"] == valor and atual["norma_id"] == norma_id and (atual["nota"] or None) == (nota or None):
        return "igual"
    if atual and atual["vigente_desde"] and vigente_desde and vigente_desde < atual["vigente_desde"]:
        return "mais_antiga"  # alteração anterior à versão vigente: não sobrescreve
    if atual:
        con.execute("UPDATE parametros SET vigente_ate=? WHERE id=?", (vigente_desde, atual["id"]))
    con.execute("""INSERT INTO parametros (cidade, zona, parametro, valor, nota, norma_id, dispositivo,
                   fonte, vigente_desde, vigente_ate, origem, conferido, criado_em)
                   VALUES (?,?,?,?,?,?,?,?,?,NULL,?,?,?)""",
                (cidade, zona, parametro, valor, nota, norma_id, dispositivo, fonte,
                 vigente_desde, origem, conferido, agora()))
    return "novo" if not atual else "alterado"


def historico(con, cidade, zona, parametro):
    return [dict(r) for r in con.execute(
        """SELECT * FROM parametros WHERE cidade=? AND zona=? AND parametro=?
           ORDER BY vigente_desde""", (cidade, zona, parametro))]


def gravar_aviso(con, cidade, norma_id, data, fonte, url, trecho):
    cur = con.execute("""INSERT OR IGNORE INTO avisos (cidade, norma_id, data, fonte, url, trecho, criado_em)
                         VALUES (?,?,?,?,?,?,?)""", (cidade, norma_id, data, fonte, url, trecho, agora()))
    return cur.rowcount == 1


def gravar_regra(con, cidade, r):
    con.execute("""INSERT INTO regras (cidade, zonas, texto, norma_id, dispositivo) VALUES (?,?,?,?,?)
                   ON CONFLICT(cidade, norma_id, dispositivo) DO UPDATE SET zonas=excluded.zonas, texto=excluded.texto""",
                (cidade, ",".join(r["zonas"]), r["texto"], r["norma"], r["dispositivo"]))


def regras(con, cidade, zona=None):
    out = []
    for r in con.execute("SELECT * FROM regras WHERE cidade=? ORDER BY norma_id, dispositivo", (cidade,)):
        zonas = r["zonas"].split(",")
        if zona is None or "todas" in zonas or zona in zonas:
            out.append(dict(r))
    return out
