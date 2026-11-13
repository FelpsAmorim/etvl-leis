"""Baixar leis e anexos e transformar em texto/artigos."""

import html
import io
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

UA = {"User-Agent": "evtl-leis/0.2 (+consulta de legislacao urbanistica)"}


class ErroFonte(Exception):
    pass


def baixar(origem, timeout=60):
    """Devolve (bytes, tipo) com tipo 'pdf' ou 'html'. Aceita URL ou caminho local."""
    p = Path(str(origem))
    if p.exists():
        dados = p.read_bytes()
    else:
        req = urllib.request.Request(str(origem), headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                dados = r.read()
        except ssl.SSLError as e:
            raise ErroFonte(f"certificado TLS inválido em {origem}: {e}") from e
        except Exception as e:
            raise ErroFonte(f"falha ao baixar {origem}: {e}") from e
    return dados, ("pdf" if dados[:5] == b"%PDF-" else "html")


def pdf_para_texto(dados):
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(dados)) as pdf:
            return "\n".join((pg.extract_text(layout=True) or "") for pg in pdf.pages)
    except ImportError:
        pass
    if shutil.which("pdftotext"):
        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            f.write(dados)
            f.flush()
            return subprocess.run(["pdftotext", "-layout", f.name, "-"],
                                  capture_output=True, text=True, check=True).stdout
    raise ErroFonte("Para ler PDF instale: pip install pdfplumber (ou o pdftotext).")


def texto_de(origem):
    dados, tipo = baixar(origem)
    if tipo == "pdf":
        return pdf_para_texto(dados)
    return dados.decode("utf-8", errors="replace")


class _Texto(HTMLParser):
    """Texto vigente: ignora o riscado (<s>, <strike>, <del>, line-through) e guarda links."""

    def __init__(self):
        super().__init__()
        self.partes, self.links = [], []
        self._riscado = self._ignorar = 0
        self._pilha = []
        self._link = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        risca = tag in ("s", "strike", "del") or "line-through" in (a.get("style") or "")
        self._pilha.append((tag, risca))
        if risca:
            self._riscado += 1
        if tag in ("script", "style"):
            self._ignorar += 1
        if tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4"):
            self.partes.append("\n")
        if tag == "a" and a.get("href"):
            self._link = [a["href"], ""]

    def handle_endtag(self, tag):
        while self._pilha:
            t, risca = self._pilha.pop()
            if risca:
                self._riscado -= 1
            if t == tag:
                break
        if tag in ("script", "style") and self._ignorar:
            self._ignorar -= 1
        if tag == "a" and self._link:
            self.links.append(tuple(self._link))
            self._link = None

    def handle_data(self, data):
        if self._link is not None:
            self._link[1] += data
            return  # texto de link não entra no corpo do artigo
        if not self._riscado and not self._ignorar:
            self.partes.append(data)


RE_ART = re.compile(r"^\s*Art\.?\s*(\d+)\s*[º°o]?\s*(-[A-Z])?\s*\.?\s*[-–]?\s*(.*)", re.I)
RE_NOTA = re.compile(r"\((?:Redação dada|Incluíd[oa]|Revogad[oa]|Acrescid[oa]|Alterad[oa]|Renumerad[oa])[^)]*\)", re.I)
RE_LEI_CITADA = re.compile(r"Lei(?: Complementar)?\s+n[º°o.]*\s*([\d.]+)\s*/\s*(\d{4})", re.I)


def html_para_artigos(dados):
    """Devolve (artigos, links). Cada artigo: {artigo, texto, notas, leis_alteradoras}."""
    p = _Texto()
    p.feed(dados.decode("utf-8", errors="replace") if isinstance(dados, bytes) else dados)
    linhas = [re.sub(r"\s+", " ", html.unescape(l)).strip() for l in "".join(p.partes).split("\n")]
    artigos, atual = [], None
    for l in filter(None, linhas):
        m = RE_ART.match(l)
        if m:
            atual = {"artigo": m.group(1) + (m.group(2) or ""), "texto": m.group(3), "notas": []}
            artigos.append(atual)
        elif atual:
            atual["texto"] += "\n" + l
        if atual:
            atual["notas"] += RE_NOTA.findall(l)
    for a in artigos:
        a["leis_alteradoras"] = sorted({f"{n.replace('.', '')}/{y}" for nota in a["notas"]
                                        for n, y in RE_LEI_CITADA.findall(nota)})
    links = [{"rotulo": t.strip(), "url": h} for h, t in p.links]
    return artigos, links


def anexos_na_pagina(links):
    return [l for l in links if l["url"].lower().endswith(".pdf")
            or "anexo" in l["url"].lower() or re.search(r"quadro|anexo|mapa", l["rotulo"], re.I)]
