"""Quadros de parâmetros -> linhas por zona.

Leitores por regra para leiautes conhecidos; o modelo pequeno (Claude Haiku 5.5)
só transcreve leiautes desconhecidos, e essas linhas saem com conferido=0.
"""

import json
import re

PARAMETROS = ["ca_min", "ca_bas", "ca_max", "to_ate_500", "to_acima_500", "gabarito_m",
              "recuo_frente", "recuo_fundo_lat_ate_10m", "recuo_fundo_lat_acima_10m",
              "cota_parte_m2", "permeabilidade_ate_500", "permeabilidade_acima_500"]

# ---------- São Paulo, Quadro 3 da Lei 16.402/2016 (10 colunas) ----------

SP_Q3_COLS = PARAMETROS[:10]
_ZONA = r"(Z[A-Z]{1,5}(?:-?[A-Za-z0-9]{1,3})?|AC-\d|AVP-\d)"
_VAL = r"(NA|N\.A\.|-|\d+(?:,\d+)?)(?:\s*\(([a-z])\))?"
_RE_SP_Q3 = re.compile(r"^\s*" + _ZONA + r"\s+" + r"\s+".join([_VAL] * len(SP_Q3_COLS)) + r"\s*$")
_RE_RODAPE = re.compile(r"^\s*\(([a-z])\)\s+(.+)")


def _num(v):
    return None if v in ("NA", "N.A.", "-") else float(v.replace(",", "."))


def _rodapes(texto):
    notas, atual = {}, None
    for l in texto.splitlines():
        m = _RE_RODAPE.match(l)
        if m:
            atual = m.group(1)
            notas[atual] = m.group(2).strip()
        elif atual and l.strip() and not _RE_SP_Q3.match(l):
            notas[atual] += " " + l.strip()
    return notas


def sp_quadro3(texto):
    """Devolve [{zona, parametro, valor, nota_letra, nota_texto}]."""
    notas = _rodapes(texto)
    out = []
    for l in texto.splitlines():
        m = _RE_SP_Q3.match(l)
        if not m:
            continue
        g = m.groups()
        for i, col in enumerate(SP_Q3_COLS):
            v, letra = g[1 + 2 * i], g[2 + 2 * i]
            out.append({"zona": g[0], "parametro": col, "valor": _num(v),
                        "nota": f"({letra}) {notas.get(letra, '')}".strip() if letra else None})
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


LEITORES = {"sp_quadro3": sp_quadro3}


def extrair(texto, leitor):
    """leitor: nome em LEITORES ou 'modelo'. Devolve (linhas, origem)."""
    if leitor == "modelo":
        return via_modelo(texto), "modelo"
    linhas = LEITORES[leitor](texto)
    if not linhas:
        raise ValueError(f"leitor {leitor} não reconheceu nenhuma linha; o leiaute mudou?")
    return linhas, "extrator"
