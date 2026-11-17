"""Testa se a máquina onde a ferramenta roda alcança cada fonte. É o primeiro comando
a rodar num host novo: mostra exatamente o que está liberado e o que está bloqueado."""

import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request

from . import consolidar, fontes

FIXAS = {
    "Querido Diário (vigia)": "https://api.queridodiario.ok.org.br/gazettes?size=1",
    "API do Claude (modo --modelo)": "https://api.anthropic.com/v1/models",
    "iaJus MCP": "https://mcp.iajus.com.br/mcp",
    "GeoSampa WFS (lote/zona SP)": "https://wfs.geosampa.prefeitura.sp.gov.br/geoserver/ows?service=WFS&request=GetCapabilities",
    "ArcGIS IMPLURB (lote/setor Manaus)": "https://pmm.manaus.am.gov.br/arcgis/rest/services?f=json",
}


def _e_url(u):
    return isinstance(u, str) and u.startswith(("http://", "https://"))


def alvos():
    """Só endereços da web: uma fonte citada por texto (ex.: 'DOM Natal 08/03/2022, p. 27-30',
    transcrita em cidades/) não tem o que testar."""
    out = dict(FIXAS)
    for slug in consolidar.cidades_disponiveis():
        cfg = consolidar.carregar_cidade(slug)
        for n in cfg.get("normas", []):
            if _e_url(n.get("link")):
                out[f"{cfg['cidade']}: {n['id']}"] = n["link"]
        for q in cfg.get("quadros", []):
            if _e_url(q.get("url")):
                out[f"{cfg['cidade']}: {q['rotulo']}"] = q["url"]
    return out


def testar(url, timeout=20):
    try:
        req = urllib.request.Request(url, headers=fontes.UA, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(512)
            return "ok", r.status
    except urllib.error.HTTPError as e:
        # 401/405 em API = host alcançável, só pede credencial/método
        api = "anthropic.com" in url or "iajus" in url
        return ("ok (alcançável)" if api and e.code in (400, 401, 403, 404, 405, 406) else "erro http"), e.code
    except ssl.SSLError as e:
        return "certificado inválido", str(e)[:80]
    except urllib.error.URLError as e:
        r = e.reason
        if isinstance(r, ssl.SSLError):
            return "certificado inválido", str(r)[:80]
        if isinstance(r, (socket.timeout, TimeoutError)):
            return "tempo esgotado", ""
        return "bloqueado/sem rota", str(r)[:80]
    except Exception as e:
        return "falha", str(e)[:80]


def rodar(log=print):
    resultados = []
    for nome, url in alvos().items():
        estado, det = testar(url)
        resultados.append((nome, urllib.parse.urlparse(url).netloc, estado, det))
        log(f"{'✓' if estado.startswith('ok') else '✗'} {nome:<55} {estado} {det}")
    return resultados
