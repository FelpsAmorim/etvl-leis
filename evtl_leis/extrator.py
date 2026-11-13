"""Quadros de parâmetros -> linhas por zona.

Leitores por regra para leiautes conhecidos; o modelo pequeno (Claude Haiku 5.5)
só transcreve leiautes desconhecidos, e essas linhas saem com conferido=0.
"""

import json
import re

PARAMETROS = ["ca_min", "ca_bas", "ca_max", "to_ate_500", "to_acima_500", "gabarito_m",
              "recuo_frente", "recuo_fundo_lat_ate_10m", "recuo_fundo_lat_acima_10m",
              "cota_parte_m2", "permeabilidade_ate_500", "permeabilidade_acima_500"]
# Outros parâmetros aceitos na base (leitores específicos): Quota Ambiental (SP, Quadro 3A),
# Manaus (gabarito em pavimentos, parcelamento) e usos permitidos "uso:<subcategoria>" (valor 1/0).
PARAMETROS_EXTRA = ["qa_min_500_1000", "qa_min_1000_2500", "qa_min_2500_5000", "qa_min_5000_10000",
                    "qa_min_acima_10000", "fator_alfa_vegetacao", "fator_beta_drenagem",
                    "gabarito_pav", "lote_min_m2", "testada_min_m"]


def parametro_valido(p):
    return p in PARAMETROS or p in PARAMETROS_EXTRA or p.startswith("uso:")

# ---------- São Paulo, Quadro 3 da Lei 16.402/2016 (10 colunas) ----------

SP_Q3_COLS = PARAMETROS[:10]
_ZONA = r"(Z[A-Z]{1,5}(?:-?[A-Za-z0-9]{1,4})?|AC-\d|AVP-\d)"
# Nota "(x)" só se cola a número; "NA" e "(k)" sozinhos são células inteiras.
_VAL = r"(?:(\d+(?:,\d+)?)(?:\s*\(([a-z])\))?|(NA|N\.A\.|-|\([a-z]\)))"
# O PDF tem rótulos verticais ("TRANSFORMAÇÃO", "ZEU", "ZER"...) à esquerda da zona; por isso
# a linha é buscada (search) e não ancorada no início. A zona pode trazer nota: "ZEUP (b)".
_RE_SP_Q3 = re.compile(r"(?<![A-Za-z0-9-])" + _ZONA + r"(?:\s*\(([a-z])\))?\s+"
                       + r"\s+".join([_VAL] * len(SP_Q3_COLS)) + r"\s*$")
_RE_RODAPE = re.compile(r"^\s*\(([a-z])\)\s+(.+)")


def _num(v):
    return None if v in ("NA", "N.A.", "-") or v.startswith("(") else float(v.replace(",", "."))


def _rodapes(texto):
    notas, atual = {}, None
    for l in texto.splitlines():
        m = _RE_RODAPE.match(l)
        if m:
            atual = m.group(1)
            notas[atual] = m.group(2).strip()
        elif atual and l.strip() and not _RE_SP_Q3.search(l):
            notas[atual] += " " + l.strip()
    return notas


def sp_quadro3(texto):
    """Devolve [{zona, parametro, valor, nota_letra, nota_texto}]."""
    notas = _rodapes(texto)
    out = []
    for l in texto.splitlines():
        m = _RE_SP_Q3.search(l)
        if not m:
            continue
        g = m.groups()
        zona, letra_zona = g[0], g[1]
        for i, col in enumerate(SP_Q3_COLS):
            num, letra, outro = g[2 + 3 * i], g[3 + 3 * i], g[4 + 3 * i]
            v = num if num is not None else outro
            if v.startswith("("):  # célula só com nota, ex.: "(k)"
                letra = v.strip("()")
            letras = [x for x in (letra_zona, letra) if x]
            out.append({"zona": zona, "parametro": col, "valor": _num(v),
                        "nota": " ".join(f"({x}) {notas.get(x, '')}".strip() for x in letras) or None})
    return out


# ---------- leiaute desconhecido: transcrição pelo modelo ----------

_SCHEMA = {
    "type": "object",
    "properties": {"linhas": {"type": "array", "items": {
        "type": "object",
        "properties": {"zona": {"type": "string"},
                       "parametro": {"type": "string", "enum": PARAMETROS},
                       "valor": {"type": ["number", "null"]},
                       "nota": {"type": ["string", "null"]},
                       "dispositivo": {"type": ["string", "null"]}},
        "required": ["zona", "parametro", "valor", "nota", "dispositivo"],
        "additionalProperties": False}}},
    "required": ["linhas"], "additionalProperties": False,
}

_PROMPT = """Você transcreve parâmetros urbanísticos de leis municipais brasileiras.
Do texto abaixo, liste cada parâmetro por zona. Não calcule nem invente: copie os
números como estão (vírgula decimal vira ponto); use valor null para NA, traço ou
"não se aplica". Parâmetros possíveis: ca_min, ca_bas, ca_max (coeficientes de
aproveitamento; se a lei tiver só "CA máximo total", use ca_max), to_ate_500 e
to_acima_500 (taxa de ocupação; se houver uma só, repita nas duas), gabarito_m
(altura máxima em metros; se for em pavimentos, escreva isso na nota e deixe valor
null), recuo_frente, recuo_fundo_lat_ate_10m, recuo_fundo_lat_acima_10m (recuo
lateral único: repita nas duas), cota_parte_m2, permeabilidade_ate_500 e
permeabilidade_acima_500 (taxa de permeabilidade; se única, repita). Em "nota" copie
notas de rodapé ou condições que mudam o valor. Em "dispositivo" diga o artigo,
anexo ou quadro de onde veio, se o texto mostrar.

TEXTO:
"""


def via_modelo(texto, modelo="claude-haiku-5-5"):
    import anthropic
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=modelo,
        max_tokens=16000,
        output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        messages=[{"role": "user", "content": _PROMPT + texto}],
    )
    if resp.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"modelo parou com stop_reason={resp.stop_reason}")
    dados = json.loads(next(b.text for b in resp.content if b.type == "text"))
    return dados["linhas"]


# ---------- São Paulo, Quadro 3A da Lei 16.402/2016 (Quota Ambiental, por perímetro PA) ----------

SP_Q3A_COLS = ["permeabilidade_ate_500", "permeabilidade_acima_500", "qa_min_500_1000", "qa_min_1000_2500",
               "qa_min_2500_5000", "qa_min_5000_10000", "qa_min_acima_10000", "fator_alfa_vegetacao",
               "fator_beta_drenagem"]
_RE_SP_Q3A = re.compile(r"^\s*(PA\s*\d+)(?:\s*\(([a-z])\))?\s+" + r"\s+".join([r"(NA|\d+(?:,\d+)?)"] * 9) + r"\s*$")
_GRUPOS_SP = {"ZCOR": ["ZCOR-1", "ZCOR-2", "ZCOR-3", "ZCORa"], "ZER": ["ZER-1", "ZER-2", "ZERa"]}


def sp_quadro3a(texto):
    """Perímetros de Qualificação Ambiental (zona = 'PA n') e, pela nota (a), a permeabilidade
    fixa de ZEPAM, ZPDSr, ZPDS, ZCOR, ZPR e ZER (zona = a própria zona)."""
    notas = {}
    atual = None
    for l in texto.splitlines():
        m = re.match(r"^\s*\(([a-z])\)\s+(.+)", l)
        if m:
            atual = m.group(1)
            notas[atual] = m.group(2).strip()
        elif atual and l.strip() and not _RE_SP_Q3A.match(l):
            notas[atual] += " " + l.strip()
    out = []
    for l in texto.splitlines():
        m = _RE_SP_Q3A.match(l)
        if not m:
            continue
        pa, letra = re.sub(r"\s+", " ", m.group(1)), m.group(2)
        for col, v in zip(SP_Q3A_COLS, m.groups()[2:]):
            out.append({"zona": pa, "parametro": col, "valor": _num(v),
                        "nota": f"({letra}) {notas.get(letra, '')}" if letra else None})
    na = notas.get("a", "")
    m = re.search(r"inseridos em (.+?) deverão.+?mínima:\s*(.+?)(?:,\s*respectivamente|$)", na)
    if m:
        zonas = re.split(r",\s*|\s+e\s+", m.group(1).strip())
        vals = re.findall(r"\d+,\d+", m.group(2))
        if len(zonas) == len(vals):
            for z, v in zip(zonas, vals):
                for zz in _GRUPOS_SP.get(z, [z]):
                    for col in SP_Q3A_COLS[:2]:
                        out.append({"zona": zz, "parametro": col, "valor": _num(v),
                                    "nota": "(a) Permeabilidade fixa da zona, independente do tamanho do lote e do PA.",
                                    "dispositivo": "Quadro 3A, nota (a)"})
    return out


# ---------- São Paulo, Quadro 4 da Lei 16.402/2016 (usos permitidos por zona) ----------
# Lido como tabela (pdfplumber). Cada célula SIM/NÃO vira parametro "uso:<subcategoria>",
# valor 1 (permitido) ou 0. A k-ésima célula SIM/NÃO da linha casa com a k-ésima zona do
# cabeçalho, porque as colunas do PDF não se alinham célula a célula.

_RE_Q4_Z = re.compile(r"^(Z[A-Z]{1,5}(?:-?[A-Za-z0-9]{1,4})?|AVP-\d|AC-\d|AIa?)$")
_RE_Q4_COD = re.compile(r"^(R1|R2[hv]-\d|EHIS|EHMP|nR[a-z0-9]*-\d+|Ind-\d[a-z]?|[A-Za-z]{1,6}-\d+[a-z]?)$")
_RE_Q4_V = re.compile(r"^(SIM|N?ÃO)\s*(?:\(([a-z])\))?$")


def _q4_zonas(t, cel, ccod, topo):
    """Nomes das zonas na ordem das colunas. A linha mais cheia de nomes manda; colunas em que
    ela está vazia herdam um nome de cima ("ZPR", "ZEPAM (k)") ou partido em duas ("ZC-"+"ZEIS")."""
    RE_Z = _RE_Q4_Z
    esp = max(range(topo), key=lambda r: sum(1 for c in range(len(t[r])) if RE_Z.match(cel(r, c))))
    especificas = {cel(esp, c) for c in range(len(t[esp])) if RE_Z.match(cel(esp, c))}
    zonas = []
    for c in range(ccod + 1, len(t[esp])):
        v = cel(esp, c)
        if RE_Z.match(v):
            zonas.append(v)
            continue
        frag = [cel(r, c) for r in range(topo) if cel(r, c)]
        nome = None
        for i, f in enumerate(frag):  # de cima para baixo; fica o último (mais específico)
            if f.endswith("-") and i + 1 < len(frag) and RE_Z.match(f + frag[i + 1]):
                nome = f + frag[i + 1]
            elif RE_Z.match(f) and not (i and frag[i - 1].endswith("-")):
                nome = f
        # rótulo de grupo ("ZM", "ZER") repetido acima das subzonas não é coluna própria
        if nome and not any(e == nome or e.startswith(nome) for e in especificas):
            zonas.append(nome)
    return zonas


def sp_quadro4_tabelas(tabelas):
    """tabelas: [(folha, tabela pdfplumber)]. Devolve (linhas_brutas, falhas)."""
    import collections
    RE_Z, RE_COD, RE_V = _RE_Q4_Z, _RE_Q4_COD, _RE_Q4_V
    out, falhas = [], []
    for folha, t in tabelas:
        def bruto(r, c):
            return (t[r][c] or "").replace("\n", "").strip() if c < len(t[r]) else ""

        def cel(r, c):
            return re.sub(r"\s*\([a-z]\)\s*$", "", bruto(r, c))

        cnt = collections.Counter(c for r in range(len(t)) for c in range(len(t[r]))
                                  if RE_COD.match(cel(r, c)) and not RE_Z.match(cel(r, c)))
        if not cnt:
            continue
        ccod = cnt.most_common(1)[0][0]
        linhas = [r for r in range(len(t)) if RE_COD.match(cel(r, ccod)) and not RE_Z.match(cel(r, ccod))]
        zonas = _q4_zonas(t, cel, ccod, linhas[0])
        for r in linhas:
            vals = [m for m in (RE_V.match(bruto(r, c)) for c in range(ccod + 1, len(t[r]))) if m]
            if len(vals) != len(zonas):
                falhas.append((folha, cel(r, ccod), len(vals), len(zonas)))
                continue
            for z, m in zip(zonas, vals):
                out.append({"zona": z, "uso": cel(r, ccod), "permitido": m.group(1) == "SIM",
                            "nota": m.group(2), "folha": folha})
    return out, falhas


def sp_quadro4(dados):
    import io
    import pdfplumber
    with pdfplumber.open(io.BytesIO(dados)) as pdf:
        texto = "\n".join((pg.extract_text() or "") for pg in pdf.pages)
        tabelas = [(pg.page_number, t) for pg in pdf.pages for t in pg.extract_tables()]
    out, falhas = sp_quadro4_tabelas(tabelas)
    if falhas:
        raise ValueError(f"Quadro 4: {len(falhas)} linhas com nº de células diferente do nº de zonas: {falhas[:5]}")
    notas = {}
    for m in re.finditer(r"^\s*\(([a-z])\)\s+(.+)$", texto, re.M):
        notas.setdefault(m.group(1), m.group(2).strip())
    return [{"zona": o["zona"], "parametro": "uso:" + o["uso"], "valor": 1.0 if o["permitido"] else 0.0,
             "nota": f"({o['nota']}) {notas.get(o['nota'], '')}".strip() if o["nota"] else None,
             "dispositivo": f"Quadro 4, folha {o['folha']}"} for o in out]


# ---------- Manaus: camada SETORES/SUBSETORES do ArcGIS da IMPLURB (Anexo I da Lei 1.838/2014) ----------

_RE_CABT = re.compile(r"CABT\s*=\s*(\d+(?:,\d+)?)")


def _num_br(v):
    if v is None:
        return None
    v = str(v).strip().replace(".", "").replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None


def manaus_setores(texto):
    """JSON de uma query ArcGIS (f=json) das camadas SETORES ou SUBSETORES.
    Gabarito do Anexo I é em pavimentos (gabarito_pav), não em metros."""
    dados = json.loads(texto)
    out = []
    for f in dados.get("features", []):
        a = f["attributes"]
        if "SETOR" in a:  # camada SETORES
            if not a.get("SETOR"):
                continue  # zonas turísticas (ZT) sem parâmetros na camada
            zona = f"Setor {a['SETOR']}"
            camt, gab, outorga = a.get("ANXI_CAMT"), a.get("ANXI_GAB_MAX"), a.get("ANXI_OUTRG_ONER")
            dens, vert = a.get("ANXI_DENSID"), a.get("ANXI_OCUP_VERTIC")
            obs = [f"Bairros: {a['BAIRRO']}" if a.get("BAIRRO") else None, a.get("ANXI_APRV_7COMAR")]
            lote_min, testada = a.get("PARCSOLO_LOTE_AREA_MIN"), a.get("PARCSOLO_LOTE_TEST_MIN")
        elif "SETOR_" in a:  # camada SUBSETORES
            zona = a["SETOR_"].strip().title().replace("Subsetor ", "Subsetor ")
            camt, gab, outorga = a.get("CAMT_"), a.get("GABARITO_MAXIMO"), a.get("OUTORGA_ONEROSA_")
            dens, vert = a.get("DENSIDADE_"), a.get("OCUPACAO_VERTICALIZACAO")
            obs, lote_min, testada = [], None, None
        else:
            continue
        base = "; ".join(filter(None, [f"densidade {dens}" if dens else None,
                                        f"ocupação {vert}" if vert else None] + obs)) or None
        out.append({"zona": zona, "parametro": "ca_max", "valor": _num_br(camt),
                    "nota": "CAMT (coeficiente de aproveitamento máximo do terreno). " + (base or ""),
                    "dispositivo": "Anexo I (camada IMPLURB)"})
        m = _RE_CABT.search(outorga or "")
        if m:
            out.append({"zona": zona, "parametro": "ca_bas", "valor": _num_br(m.group(1)),
                        "nota": "CABT; outorga onerosa e transferência do direito de construir admitidas.",
                        "dispositivo": "Anexo I (camada IMPLURB)"})
        out.append({"zona": zona, "parametro": "gabarito_pav", "valor": _num_br(gab),
                    "nota": "Gabarito máximo em pavimentos (conferir unidade no Anexo I).",
                    "dispositivo": "Anexo I (camada IMPLURB)"})
        if lote_min:
            out.append({"zona": zona, "parametro": "lote_min_m2", "valor": _num_br(lote_min), "nota": None,
                        "dispositivo": "Parcelamento do solo (camada IMPLURB)"})
        if testada:
            out.append({"zona": zona, "parametro": "testada_min_m", "valor": _num_br(testada), "nota": None,
                        "dispositivo": "Parcelamento do solo (camada IMPLURB)"})
    return out


LEITORES = {"sp_quadro3": sp_quadro3, "sp_quadro3a": sp_quadro3a, "manaus_setores": manaus_setores}
LEITORES_PDF = {"sp_quadro4": sp_quadro4}  # recebem os bytes do PDF (leem tabelas)


def extrair(texto, leitor):
    """leitor: nome em LEITORES (texto), LEITORES_PDF (bytes do PDF) ou 'modelo'. Devolve (linhas, origem)."""
    if leitor == "modelo":
        return via_modelo(texto), "modelo"
    linhas = (LEITORES_PDF[leitor] if leitor in LEITORES_PDF else LEITORES[leitor])(texto)
    if not linhas:
        raise ValueError(f"leitor {leitor} não reconheceu nenhuma linha; o leiaute mudou?")
    return linhas, "extrator"
