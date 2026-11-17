"""Zona de um lote em São Paulo pelo GeoSampa (WFS aberto da prefeitura).

Camada vigente: geoportal:perimetro_zona_lei_18177_24 (Mapa 1 da Lei 18.177/2024,
que substituiu o mapa da Lei 18.081/2024). Para comparação, a camada de 2016
(Lei 16.402) é geoportal:zoneamento_2016_map1.

O lote vem de geoportal:lote_cidadao (SQL = setor.quadra.lote-dígito, ou endereço).
Coordenadas em EPSG:31983 (SIRGAS 2000 / UTM 23S).
"""

import json
import math
import re
import unicodedata
import urllib.parse
import urllib.request

from .fontes import UA, ErroFonte

WFS = "https://wfs.geosampa.prefeitura.sp.gov.br/geoserver/ows"
CAMADA_LOTE = "lote_cidadao"
CAMADAS_ZONA = {
    "2024": {"camada": "perimetro_zona_lei_18177_24", "norma_id": "LEI_18177_2024",
             "descricao": "Mapa 1 da Lei 18.177/2024 (vigente)"},
    "2016": {"camada": "zoneamento_2016_map1", "norma_id": "LEI_16402_2016",
             "descricao": "Mapa 1 da Lei 16.402/2016 (substituído)"},
}
CAMPOS_LOTE = ("cd_setor_fiscal,cd_quadra_fiscal,cd_lote,cd_condominio,cd_digito_sql,tx_tipo_lote,"
               "nm_logradouro_completo,cd_numero_porta,qt_area_terreno,ge_poligono")


# ---------- acesso ao WFS (trocável nos testes) ----------

def wfs(camada, cql, campos=None, limite=20):
    p = {"service": "WFS", "version": "2.0.0", "request": "GetFeature",
         "typeNames": "geoportal:" + camada, "outputFormat": "application/json",
         "count": limite, "CQL_FILTER": cql}
    if campos:
        p["propertyName"] = campos
    url = WFS + "?" + urllib.parse.urlencode(p)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
            corpo = r.read()
    except Exception as e:
        raise ErroFonte(f"GeoSampa ({camada}): {e}") from e
    try:
        return json.loads(corpo)["features"]
    except Exception as e:
        raise ErroFonte(f"GeoSampa ({camada}) respondeu algo inesperado: {corpo[:200]!r}") from e


def url_camada(camada):
    return f"{WFS}?service=WFS&version=2.0.0&request=GetFeature&typeNames=geoportal:{camada}"


# ---------- entradas ----------

RE_SQL = re.compile(r"^\s*(\d{3})\D?(\d{3})\D?(\d{4})(?:\D?(\d))?\s*$")


def ler_sql(sql):
    """'012.345.0067-8' -> ('012', '345', '0067', '8'). Dígito opcional."""
    m = RE_SQL.match(sql or "")
    if not m:
        raise ValueError(f"SQL inválido: {sql!r}. Use setor.quadra.lote-dígito, ex.: 009.001.0074-1")
    return m.groups()


_TIPOS = {"RUA": "R", "AVENIDA": "AV", "ALAMEDA": "AL", "TRAVESSA": "TV", "PRACA": "PC",
          "ESTRADA": "EST", "LARGO": "LG", "VIELA": "VL", "RODOVIA": "ROD", "R": "R", "AV": "AV"}


def normalizar_logradouro(txt):
    """'Rua Samuel das Neves' -> 'SAMUEL DAS NEVES' (sem tipo, sem acento, maiúsculas)."""
    t = unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode().upper()
    t = re.sub(r"[^A-Z0-9 ]", " ", t)
    partes = t.split()
    if partes and partes[0].rstrip(".") in _TIPOS:
        partes = partes[1:]
    return " ".join(partes)


def latlon_para_utm23s(lat, lon):
    """WGS84/SIRGAS2000 -> UTM fuso 23S (EPSG:31983). Erro < 1 m em SP."""
    a, f = 6378137.0, 1 / 298.257222101
    k0, lon0 = 0.9996, math.radians(-45.0)
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    phi, lam = math.radians(lat), math.radians(lon)
    n = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    t = math.tan(phi) ** 2
    c = ep2 * math.cos(phi) ** 2
    A = math.cos(phi) * (lam - lon0)
    m = a * ((1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * phi
             - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * math.sin(2 * phi)
             + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * math.sin(4 * phi)
             - (35 * e2 ** 3 / 3072) * math.sin(6 * phi))
    x = k0 * n * (A + (1 - t + c) * A ** 3 / 6 + (5 - 18 * t + t * t + 72 * c - 58 * ep2) * A ** 5 / 120) + 500000
    y = k0 * (m + n * math.tan(phi) * (A * A / 2 + (5 - t + 9 * c + 4 * c * c) * A ** 4 / 24
                                       + (61 - 58 * t + t * t + 600 * c - 330 * ep2) * A ** 6 / 720))
    return x, y + 10000000


# ---------- geometria simples (sem dependências) ----------

def aneis(geom):
    """Lista de polígonos [[anel_externo, buracos...], ...] de Polygon/MultiPolygon GeoJSON."""
    if geom["type"] == "Polygon":
        return [geom["coordinates"]]
    if geom["type"] == "MultiPolygon":
        return geom["coordinates"]
    raise ValueError(f"geometria não suportada: {geom['type']}")


def _no_anel(x, y, anel):
    dentro = False
    j = len(anel) - 1
    for i in range(len(anel)):
        xi, yi = anel[i][:2]
        xj, yj = anel[j][:2]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            dentro = not dentro
        j = i
    return dentro


def contem(geom, x, y):
    for poli in aneis(geom):
        if _no_anel(x, y, poli[0]) and not any(_no_anel(x, y, b) for b in poli[1:]):
            return True
    return False


def amostras(geom, n=20):
    """Pontos de uma grade n×n que caem dentro do polígono (para estimar fração por zona)."""
    xs = [p[0] for poli in aneis(geom) for p in poli[0]]
    ys = [p[1] for poli in aneis(geom) for p in poli[0]]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    pts = [(x0 + (x1 - x0) * (i + .5) / n, y0 + (y1 - y0) * (j + .5) / n) for i in range(n) for j in range(n)]
    return [p for p in pts if contem(geom, *p)]


def wkt(geom):
    def anel(a):
        return "(" + ", ".join(f"{p[0]:.3f} {p[1]:.3f}" for p in a) + ")"
    polis = aneis(geom)
    if len(polis) == 1:
        return "POLYGON(" + ", ".join(anel(a) for a in polis[0]) + ")"
    return "MULTIPOLYGON(" + ", ".join("(" + ", ".join(anel(a) for a in p) + ")" for p in polis) + ")"


# ---------- consulta ----------

def _cql_txt(s):
    return s.replace("'", "''")


def achar_lote(sql=None, endereco=None, numero=None, x=None, y=None, get=wfs):
    if sql:
        setor, quadra, lote, _ = ler_sql(sql)
        cql = f"cd_setor_fiscal='{setor}' AND cd_quadra_fiscal='{quadra}' AND cd_lote='{lote}'"
    elif endereco:
        nome = _cql_txt(normalizar_logradouro(endereco))
        cql = f"(nm_logradouro_completo ILIKE '% {nome}' OR nm_logradouro_completo ILIKE '{nome}')"
        if numero:
            cql += f" AND cd_numero_porta='{_cql_txt(str(numero).lstrip('0'))}'"
    elif x is not None:
        cql = f"INTERSECTS(ge_poligono, POINT({x:.3f} {y:.3f}))"
    else:
        raise ValueError("informe sql, endereço ou coordenada")
    lotes = get(CAMADA_LOTE, cql, CAMPOS_LOTE)
    # lote fiscal antes do municipal (o municipal é a sobra da quadra)
    lotes.sort(key=lambda f: f["properties"].get("tx_tipo_lote") != "FISCAL")
    return lotes


def zonas_em(geom_ou_ponto, versao="2024", get=wfs):
    """Zonas que tocam o lote (com fração estimada da área) ou que contêm o ponto."""
    cfg = CAMADAS_ZONA[versao]
    campos = "cd_zoneamento_perimetro,tx_zoneamento_perimetro"
    if isinstance(geom_ou_ponto, tuple):
        x, y = geom_ou_ponto
        feats = get(cfg["camada"], f"INTERSECTS(ge_poligono, POINT({x:.3f} {y:.3f}))", campos)
        return [{"zona": f["properties"]["cd_zoneamento_perimetro"],
                 "nome": f["properties"]["tx_zoneamento_perimetro"], "fracao": 1.0} for f in feats[:1]]
    geom = geom_ou_ponto
    feats = get(cfg["camada"], f"INTERSECTS(ge_poligono, {wkt(geom)})", campos)
    if len(feats) <= 1:
        return [{"zona": f["properties"]["cd_zoneamento_perimetro"],
                 "nome": f["properties"]["tx_zoneamento_perimetro"], "fracao": 1.0} for f in feats]
    # lote em mais de uma zona: estima a fração por amostragem de pontos, uma consulta por ponto
    pts = amostras(geom, n=6) or amostras(geom, n=12)
    conta = {}
    for p in pts:
        for z in zonas_em(p, versao, get):
            conta[(z["zona"], z["nome"])] = conta.get((z["zona"], z["nome"]), 0) + 1
    tot = sum(conta.values()) or 1
    return sorted(({"zona": k[0], "nome": k[1], "fracao": round(v / tot, 2)} for k, v in conta.items()),
                  key=lambda z: -z["fracao"])


# Macroárea do PDE (Mapa 2 na redação da Lei 18.209/2024). O GeoSampa não publica os subsetores da
# MEM (Arco Tietê, Arco Pinheiros...): conferido em 17/11/2026 nas 488 camadas do WFS. Então só dá para
# afirmar que o lote NÃO está no Arco Tietê (fora da MEM); dentro da MEM, quem confirma é o usuário.
CAMADA_MACROAREA = "pde_macroarea_lei_18209"


def _sem_acento(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().upper()


def _cql_geo(geom_ou_ponto):
    if isinstance(geom_ou_ponto, tuple):
        return f"POINT({geom_ou_ponto[0]:.3f} {geom_ou_ponto[1]:.3f})"
    return wkt(geom_ou_ponto)


def macroarea(geom_ou_ponto, get=wfs):
    """[{'sigla': 'MEM', 'nome': ...}] das macroáreas que o lote toca; None se a camada não respondeu."""
    try:
        feats = get(CAMADA_MACROAREA, f"INTERSECTS(ge_poligono, {_cql_geo(geom_ou_ponto)})",
                    "sg_macroarea,nm_macroarea")
    except ErroFonte:
        return None
    return [{"sigla": (f["properties"].get("sg_macroarea") or "").strip(),
             "nome": f["properties"].get("nm_macroarea")} for f in feats]


def arco_tiete(macros):
    """False: fora da MEM, logo fora do Arco Tietê. None: na MEM (subsetor sem camada) ou sem resposta."""
    if macros is None:
        return None
    if any(m["sigla"] == "MEM" for m in macros):
        return None
    return False


# Sobreposições que o GeoSampa publica (camadas e campos conferidos em 17/11/2026 nos lotes da
# Porto Seguro e da Tecelagem). Elas não mudam o número do Quadro 3, mas condicionam o projeto.
SOBREPOSICOES = [
    ("envoltoria_condephaat", "patrimonio_cultural_area_envoltoria_CONDEPHAAT", "nm_area", "tx_resolucao_condephaat",
     "Área envoltória CONDEPHAAT: projeto depende de anuência prévia (gabarito, volumetria, materiais)."),
    ("envoltoria_conpresp", "patrimonio_cultural_area_envoltoria_CONPRESP", "nm_area", "tx_resolucao_conpresp",
     "Área envoltória CONPRESP: projeto depende de anuência prévia."),
    ("envoltoria_iphan", "patrimonio_cultural_area_envoltoria_IPHAN", "nm_area", "tx_resolucao_iphan",
     "Área envoltória IPHAN: projeto depende de anuência prévia."),
    ("operacao_urbana", "operacao_urbana", "nm_operacao_urbana", "tx_lei_operacao_urbana",
     "Operação urbana: o potencial adicional segue a lei da operação (ex.: CEPAC), não a outorga comum."),
    ("subsetor_operacao_urbana", "subsetor_operacao_urbana", None, None, None),
    ("aiu", "perimetro_aiu", "nm_lei", "cd_numero_lei",
     "Área de Intervenção Urbana: conferir os parâmetros e incentivos da lei da AIU."),
    ("requalifica_centro", "requalifica_centro_perimetro_geral", "nm_perimetro", "dc_lei",
     "Perímetro do Requalifica Centro (Lei 17.577/2021): regras próprias para reforma e mudança de uso."),
]
CAMADA_TOMBADO = "patrimonio_cultural_bem_tombado"


def _texto(props, campo):
    if campo and props.get(campo):
        return str(props[campo]).strip()
    for k, v in props.items():  # campo desconhecido: o primeiro texto que pareça nome
        if isinstance(v, str) and k.startswith(("nm_", "tx_", "sg_")):
            return v.strip()
    return None


def sobreposicoes(geom_ou_ponto, sql_partes=None, get=wfs):
    """Lista de {tipo, nome, norma, camada, aviso}. Camada que não responder entra com 'erro'."""
    alvo = _cql_geo(geom_ou_ponto)
    out = []
    for tipo, camada, campo_nome, campo_norma, aviso in SOBREPOSICOES:
        try:
            feats = get(camada, f"INTERSECTS(ge_poligono, {alvo})", None)
        except ErroFonte as e:
            out.append({"tipo": tipo, "camada": camada, "erro": str(e)[:120]})
            continue
        for f in feats:
            p = f.get("properties") or {}
            out.append({"tipo": tipo, "camada": camada, "nome": _texto(p, campo_nome),
                        "norma": p.get(campo_norma) if campo_norma else None, "aviso": aviso})
    if sql_partes:  # tombamento: só o próprio lote (o vizinho tombado encosta na divisa e cairia no INTERSECTS)
        setor, quadra, lt = sql_partes[:3]
        try:
            feats = get(CAMADA_TOMBADO, f"cd_setor_fiscal='{setor}' AND cd_quadra_fiscal='{quadra}' AND cd_lote='{lt}'", None)
            for f in feats:
                p = f.get("properties") or {}
                out.append({"tipo": "tombado", "camada": CAMADA_TOMBADO, "nome": p.get("nm_area_tombada"),
                            "norma": " | ".join(x for x in (p.get("tx_resolucao_conpresp"), p.get("tx_resolucao_condephaat"))
                                                if x and x != "não consta") or None,
                            "aviso": "Bem tombado: qualquer intervenção depende de anuência prévia; demolição em regra vedada."})
        except ErroFonte as e:
            out.append({"tipo": "tombado", "camada": CAMADA_TOMBADO, "erro": str(e)[:120]})
    return out


def zona(sql=None, endereco=None, numero=None, lat=None, lon=None, comparar_2016=True, get=wfs):
    x = y = None
    if lat is not None:
        x, y = latlon_para_utm23s(lat, lon)
    lotes = achar_lote(sql, endereco, numero, x, y, get=get)
    res = {"cidade": "São Paulo", "entrada": {k: v for k, v in
           {"sql": sql, "endereco": endereco, "numero": numero, "lat": lat, "lon": lon}.items() if v is not None}}
    if lotes:
        p = lotes[0]["properties"]
        res["lote"] = {"sql": f"{p['cd_setor_fiscal']}.{p['cd_quadra_fiscal']}.{p['cd_lote']}"
                              + (f"-{p['cd_digito_sql']}" if p.get("cd_digito_sql") else ""),
                       "condominio": p.get("cd_condominio"), "tipo": p.get("tx_tipo_lote"),
                       "endereco": " ".join(filter(None, [p.get("nm_logradouro_completo"), p.get("cd_numero_porta")])) or None,
                       "area_terreno_m2": p.get("qt_area_terreno")}
        if len(lotes) > 1:
            res["outros_lotes"] = len(lotes) - 1
        alvo = lotes[0]["geometry"]
    elif x is not None:
        res["lote"] = None
        res["aviso"] = "Ponto fora de lote cadastrado (rua ou área pública); zona do ponto."
        alvo = (x, y)
    else:
        raise ErroFonte("Lote não encontrado no GeoSampa (lote_cidadao). Unidades de condomínio: "
                        "use o endereço ou a coordenada do terreno.")
    versoes = ["2024", "2016"] if comparar_2016 else ["2024"]
    for v in versoes:
        cfg = CAMADAS_ZONA[v]
        res[f"zonas_{v}"] = zonas_em(alvo, v, get)
        res[f"fonte_{v}"] = {"camada": "geoportal:" + cfg["camada"], "norma_id": cfg["norma_id"],
                             "descricao": cfg["descricao"], "url": url_camada(cfg["camada"])}
    if comparar_2016:
        z24 = [z["zona"] for z in res["zonas_2024"]]
        z16 = [z["zona"] for z in res["zonas_2016"]]
        res["mudou_desde_2016"] = z24 != z16
    res["zona"] = res["zonas_2024"][0]["zona"] if res["zonas_2024"] else None
    res["macroarea"] = macroarea(alvo, get)
    res["arco_tiete"] = arco_tiete(res["macroarea"])
    partes = None
    if lotes:
        p = lotes[0]["properties"]
        partes = (p["cd_setor_fiscal"], p["cd_quadra_fiscal"], p["cd_lote"])
    res["sobreposicoes"] = sobreposicoes(alvo, partes, get)
    return res
