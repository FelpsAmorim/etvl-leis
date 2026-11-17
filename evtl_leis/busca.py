"""Busca do lote: de um endereço/coordenada/inscrição até a zona e os parâmetros na base.

São Paulo: GeoSampa (ver lote.py), zona pelo Mapa 1 da Lei 18.177/2024.
Manaus:    ArcGIS da IMPLURB (camadas SETORES, SUBSETORES, LOTES, BAIRROS) e geocodificador
           da prefeitura; a "zona" é o setor (e o subsetor, quando houver) do Anexo I da Lei 1.838/2014.
Natal:     não há serviço aberto com as bacias de esgotamento (Mapas 2/2A); o usuário informa
           bairro e, quando souber, bacia e eixo estruturante.
"""

import json
import unicodedata
import urllib.parse
import urllib.request

from . import base, lote
from .fontes import UA, ErroFonte

# ---------- Manaus ----------

IMPLURB = "https://pmm.manaus.am.gov.br/arcgis/rest/services/IMPLURB/IMPLURB_PATRIMONIO_HISTORICO_SRGS/MapServer"
GEOCODE_MAO = "https://pmm.manaus.am.gov.br/arcgis/rest/services/GEOCODE/GeocodeManauaraDEF_07102025/GeocodeServer"
CAMADAS_MAO = {"lotes": 3, "subsetores": 4, "setores": 5, "bairros": 6}


def arcgis(url, params):
    u = url + "?" + urllib.parse.urlencode({**params, "f": "json"})
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60) as r:
            d = json.loads(r.read())
    except Exception as e:
        raise ErroFonte(f"ArcGIS Manaus: {e}") from e
    if "error" in d:
        raise ErroFonte(f"ArcGIS Manaus: {d['error']}")
    return d


def _por_ponto(camada, lon, lat, campos, get, distancia=None):
    p = {"geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
         "spatialRel": "esriSpatialRelIntersects", "outFields": campos, "returnGeometry": "false"}
    if distancia:
        p.update(distance=distancia, units="esriSRUnit_Meter")
    return [f["attributes"] for f in get(f"{IMPLURB}/{CAMADAS_MAO[camada]}/query", p).get("features", [])]


def geocodificar_manaus(endereco, get=arcgis):
    d = get(f"{GEOCODE_MAO}/findAddressCandidates", {"SingleLine": endereco, "outSR": 4326, "maxLocations": 1})
    c = (d.get("candidates") or [None])[0]
    if not c or c.get("score", 0) < 80:
        raise ErroFonte(f"Endereço não encontrado no geocodificador de Manaus: {endereco!r}")
    return c["location"]["y"], c["location"]["x"], c["address"]


def manaus(lat=None, lon=None, cgl=None, endereco=None, get=arcgis):
    res = {"cidade": "Manaus", "entrada": {k: v for k, v in
           {"lat": lat, "lon": lon, "cgl": cgl, "endereco": endereco}.items() if v is not None}}
    if endereco:
        lat, lon, res["endereco_encontrado"] = geocodificar_manaus(endereco, get)
    if cgl:
        d = get(f"{IMPLURB}/{CAMADAS_MAO['lotes']}/query",
                {"where": f"CGL='{str(cgl).replace(chr(39), '')}'", "outFields": "CGL,SETOR,QUADRA,LOTE",
                 "returnGeometry": "true", "outSR": 4326, "returnCentroid": "true"})
        fs = d.get("features", [])
        if not fs:
            raise ErroFonte(f"CGL {cgl} não encontrado na camada LOTES da IMPLURB")
        anel = fs[0]["geometry"]["rings"][0]
        lon = sum(p[0] for p in anel) / len(anel)
        lat = sum(p[1] for p in anel) / len(anel)
        lotes = [fs[0]["attributes"]]
    elif lat is not None:
        lotes = _por_ponto("lotes", lon, lat, "CGL,SETOR,QUADRA,LOTE", get) or \
            _por_ponto("lotes", lon, lat, "CGL,SETOR,QUADRA,LOTE", get, distancia=20)
    else:
        raise ValueError("informe lat/lon, cgl ou endereço")
    res["ponto"] = {"lat": lat, "lon": lon}
    if lotes:
        res["lote"] = {"cgl": lotes[0].get("CGL"), "setor_fiscal": lotes[0].get("SETOR"),
                       "quadra": lotes[0].get("QUADRA"), "lote": lotes[0].get("LOTE")}
        if len(lotes) > 1:
            res["aviso"] = (f"{len(lotes)} lotes perto do ponto (o endereço caiu na rua); usei o primeiro. "
                            "Confira o CGL.")
    setores = _por_ponto("setores", lon, lat, "SETOR,BAIRRO", get)
    sub = _por_ponto("subsetores", lon, lat, "SETOR_", get)
    bai = _por_ponto("bairros", lon, lat, "NOME_BAIRR", get)
    zonas = []
    if setores and setores[0].get("SETOR"):
        zonas.append(f"Setor {setores[0]['SETOR']}")
    if sub and sub[0].get("SETOR_"):
        zonas.append(sub[0]["SETOR_"].strip().title())  # mesmo nome que o leitor manaus_setores grava
    res["bairro"] = bai[0]["NOME_BAIRR"] if bai else None
    res["zonas"] = zonas
    res["fonte"] = {"camadas": [f"{IMPLURB}/{CAMADAS_MAO[c]}" for c in ("setores", "subsetores", "lotes")],
                    "norma_id": "LEI_1838_2014"}
    if not zonas:
        res["aviso"] = "Ponto fora dos setores da IMPLURB (zona turística ou fora do perímetro urbano?)."
    return res


# ---------- Natal ----------

GEO_NATAL = "https://geo.natal.rn.gov.br/server/rest/services/Hosted"
# Camadas oficiais da prefeitura (portal ArcGIS da SEMURB). As bacias de ESGOTAMENTO dos Mapas 2/2A
# não estão publicadas (a camada Bacias_Drenagem é de drenagem, outra divisão).
CAMADAS_NATAL_SOBREPOSICAO = {
    "ZA_revetorizada": "Zona Adensável", "AEITP": "Área Especial de Interesse Turístico e Paisagístico",
    "AERU": "Área Especial de Requalificação Urbana", "AEIS": "Área Especial de Interesse Social",
    "AEPC": "Área Especial de Preservação Cultural", "AECE": "AECE", "AEM": "AEM",
    "ZPA_1_merge": "ZPA-1", "ZPA_2": "ZPA-2", "ZPA_3": "ZPA-3", "ZPA_4": "ZPA-4", "ZPA_5": "ZPA-5",
    "ZPA_6": "ZPA-6", "ZPA_7": "ZPA-7", "ZPA_8": "ZPA-8", "ZPA_9": "ZPA-9", "ZPA_10_Merge": "ZPA-10",
}


def _natal_ponto(servico, lat, lon, campos, get):
    p = {"geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
         "spatialRel": "esriSpatialRelIntersects", "outFields": campos, "returnGeometry": "false"}
    d = get(f"{GEO_NATAL}/{servico}/FeatureServer/0/query", p)
    return [f["attributes"] for f in d.get("features", [])]


def natal_por_ponto(lat, lon, get=arcgis):
    """Bairro oficial e áreas que se sobrepõem ao ponto (Zona Adensável, ZPAs, AEIS, AEITP...)."""
    b = _natal_ponto("Bairros", lat, lon, "bairro,reg_adm", get)
    sobre = [nome for serv, nome in CAMADAS_NATAL_SOBREPOSICAO.items() if _natal_ponto(serv, lat, lon, "objectid", get)]
    return (b[0]["bairro"] if b else None), (b[0]["reg_adm"] if b else None), sobre

def _sem_acento(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().upper().strip()


def natal(con, bairro=None, bacia=None, eixo=None, lat=None, lon=None, get=arcgis):
    """Bairro pelo ponto (camada oficial) ou informado; bacia e eixo informados pelo usuário,
    porque os Mapas 2/2A (bacias de esgotamento) não têm serviço aberto."""
    res = {"cidade": "Natal", "entrada": {k: v for k, v in {"bairro": bairro, "bacia": bacia, "eixo": eixo,
                                                             "lat": lat, "lon": lon}.items() if v is not None}}
    if lat is not None:
        b_ponto, regiao, sobre = natal_por_ponto(lat, lon, get)
        res["bairro_oficial"], res["regiao_administrativa"], res["sobreposicoes"] = b_ponto, regiao, sobre
        res["fonte_ponto"] = f"{GEO_NATAL}/Bairros e camadas de zonas/áreas especiais (geo.natal.rn.gov.br)"
        bairro = bairro or b_ponto
    nomes = [r[0] for r in con.execute("SELECT DISTINCT zona FROM parametros WHERE cidade='Natal'")]
    zonas, avisos = [], []

    def achar(prefixo, nome):
        alvo = _sem_acento(f"{prefixo} {nome}")
        cand = [z for z in nomes if _sem_acento(z) == alvo] or \
               [z for z in nomes if _sem_acento(z).startswith(alvo) and "/" not in z[len(prefixo) + 1:]] or \
               [z for z in nomes if alvo in _sem_acento(z) and "/" not in z]
        return cand[0] if cand else None

    b = None
    if bairro:
        b = achar("Bairro", bairro)
        if b:
            zonas.append(b)
        else:
            avisos.append(f"Bairro {bairro!r} não está nos Quadros 1.1/1.2.")
    if bacia:
        bacia = bacia.upper().strip()
        if f"Bacia {bacia}" in nomes:
            zonas.append(f"Bacia {bacia}")
        else:
            avisos.append(f"Bacia {bacia!r} não existe (use AN..QN ou AS..OS).")
        if b and f"{b} / Bacia {bacia}" in nomes:
            zonas.append(f"{b} / Bacia {bacia}")
        elif b:
            avisos.append(f"A bacia {bacia} não passa pelo {b} segundo os Quadros 1.1/1.2.")
    elif b:
        opc = sorted(z.split("/ Bacia ")[1] for z in nomes if z.startswith(b + " / Bacia "))
        if opc:
            res["bacias_do_bairro"] = opc
            avisos.append("Informe a bacia do lote (Mapas 2/2A) para o CA exato; o CA do bairro é o maior entre as bacias.")
    if eixo:
        e = [z for z in nomes if z.startswith("Eixo ") and _sem_acento(eixo) in _sem_acento(z)
             and (not bacia or z.endswith(f"/ Bacia {bacia}"))]
        if e:
            zonas += sorted(set(e))
        else:
            avisos.append(f"Eixo {eixo!r} não encontrado nos Quadros 1.3/1.4" + (f" para a bacia {bacia}." if bacia else "."))
    if res.get("sobreposicoes"):
        especiais = [x for x in res["sobreposicoes"] if x != "Zona Adensável"]
        if especiais:
            avisos.append("Lote em " + ", ".join(especiais) + ": valem as prescrições próprias dessas áreas "
                          "(regulamentação específica), que podem restringir os parâmetros abaixo.")
        if "Zona Adensável" not in res["sobreposicoes"]:
            avisos.append("Ponto fora da Zona Adensável: acima do CA básico 1,0 não há outorga onerosa (art. 13).")
    if not zonas:
        avisos.append("Sem bairro/bacia, só valem os parâmetros gerais do município.")
    zonas.append("Município (geral)")
    res["zonas"] = zonas
    res["avisos"] = avisos
    res["fonte"] = {"norma_id": "LCP_208_2022", "descricao": "Anexo II (Quadros 1.1 a 1.4); bacia do lote nos Mapas 2/2A"}
    return res


# ---------- interface comum ----------

def buscar(con, cidade, data=None, **kw):
    """data='AAAA-MM-DD': parâmetros vigentes nessa data (para comparar com laudos antigos).
    Devolve {'cidade', 'zonas', ..., 'parametros': {zona: [...]}, 'regras': [...]}."""
    if cidade == "sao-paulo":
        r = lote.zona(sql=kw.get("sql"), endereco=kw.get("endereco"), numero=kw.get("numero"),
                      lat=kw.get("lat"), lon=kw.get("lon"))
        r["zonas"] = [z["zona"] for z in r["zonas_2024"]]
    elif cidade == "manaus":
        r = manaus(lat=kw.get("lat"), lon=kw.get("lon"), cgl=kw.get("cgl"), endereco=kw.get("endereco"))
    elif cidade == "natal":
        r = natal(con, bairro=kw.get("bairro"), bacia=kw.get("bacia"), eixo=kw.get("eixo"),
                  lat=kw.get("lat"), lon=kw.get("lon"))
    else:
        raise ValueError(f"cidade sem busca: {cidade}")
    nome = r["cidade"]
    r["parametros"] = {z: base.vigente(con, nome, z, data=data) for z in r["zonas"]}
    if data:
        r["data_referencia"] = data
        r["aviso_data"] = ("Parâmetros na versão vigente nessa data. As regras textuais não têm data de "
                           "vigência na base: confira se já valiam.")
    regras, vistas = [], set()
    for z in r["zonas"]:
        for g in base.regras(con, nome, z):
            if (g["norma_id"], g["dispositivo"]) not in vistas:
                vistas.add((g["norma_id"], g["dispositivo"]))
                regras.append(g)
    r["regras"] = regras
    return r
