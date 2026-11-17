"""Testes offline: rodam sem rede. python -m unittest discover -s tests"""

import json
import sqlite3
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from evtl_leis import base, consolidar, extrator, fontes, vigia

FIX = Path(__file__).parent / "fixtures"
Q3_URL = "https://gestaourbana.prefeitura.sp.gov.br/wp-content/uploads/2016/03/005-QUADRO_3_FINAL.pdf"
Q3A_URL = "https://gestaourbana.prefeitura.sp.gov.br/wp-content/uploads/2016/03/006-QUADRO_3A_FINAL.pdf"


class TestExtrator(unittest.TestCase):
    def test_sp_quadro3(self):
        linhas = extrator.sp_quadro3((FIX / "sp_quadro3.txt").read_text(encoding="utf-8"))
        v = {(l["zona"], l["parametro"]): l for l in linhas}
        self.assertEqual(len({l["zona"] for l in linhas}), 7)
        self.assertEqual(v[("ZEM", "ca_max")]["valor"], 2.0)
        self.assertIn("igual a 4", v[("ZEM", "ca_max")]["nota"])
        self.assertEqual(v[("ZEU", "ca_max")]["valor"], 4.0)
        self.assertIsNone(v[("ZEU", "gabarito_m")]["valor"])
        self.assertEqual(v[("ZER-1", "ca_min")]["valor"], 0.05)
        self.assertIn("500m²", v[("ZEIS-3", "ca_max")]["nota"])

    def test_sp_quadro3_linhas_com_rotulo_vertical(self):
        # no PDF real, letras de rótulos verticais aparecem antes da zona
        texto = ("     M        ZEUP (b) 0,5 1   2    0,85    0,70   28   NA   NA     3 (j)  NA\n"
                 "     Ç   ZER   ZER-2 0,05  1   1    0,50    0,50   10   5    NA     3      NA\n"
                 "               ZC-ZEIS 0,5 1   2    0,85    0,70   NA    5   NA     3 (j)  NA\n"
                 "     E         AVP-1  NA  (k)  (k)   (k)    (k)    28   NA    NA    3 (j)  NA\n"
                 "      (b) Zona recepciona ZEU.\n      (k) Ver artigo 30 desta lei.\n")
        v = {(l["zona"], l["parametro"]): l for l in extrator.sp_quadro3(texto)}
        self.assertEqual({z for z, _ in v}, {"ZEUP", "ZER-2", "ZC-ZEIS", "AVP-1"})
        self.assertIn("(b)", v[("ZEUP", "ca_max")]["nota"])
        self.assertIsNone(v[("AVP-1", "ca_bas")]["valor"])
        self.assertIn("artigo 30", v[("AVP-1", "ca_bas")]["nota"])
        self.assertEqual(v[("AVP-1", "gabarito_m")]["valor"], 28.0)

    def test_leiaute_desconhecido_falha_alto(self):
        with self.assertRaises(ValueError):
            extrator.extrair("texto sem tabela", "sp_quadro3")


class TestFontes(unittest.TestCase):
    def test_html_ignora_riscado_e_guarda_notas(self):
        artigos, links = fontes.html_para_artigos((FIX / "lei_exemplo.html").read_bytes())
        self.assertEqual([a["artigo"] for a in artigos], ["1", "2"])
        self.assertNotIn("antigo", artigos[1]["texto"])
        self.assertEqual(artigos[1]["leis_alteradoras"], ["18081/2024"])
        self.assertEqual(len(fontes.anexos_na_pagina(links)), 2)


class TestConsolidacao(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = base.abrir(str(Path(self.tmp.name) / "t.db"))

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def _sp_sem_quadro4(self):
        """Config de SP sem o Quadro 4 (PDF de tabelas, testado à parte) para rodar sem rede."""
        original = consolidar.carregar_cidade

        def carregar(slug):
            cfg = original(slug)
            cfg["quadros"] = [q for q in cfg["quadros"] if q["leitor"] != "sp_quadro4"]
            return cfg
        return mock.patch.object(consolidar, "carregar_cidade", carregar)

    def test_sp_offline(self):
        locais = {Q3_URL: str(FIX / "sp_quadro3.txt"), Q3A_URL: str(FIX / "sp_quadro3a.txt")}
        with self._sp_sem_quadro4():
            r = consolidar.atualizar(self.con, "sao-paulo", origem_local=locais, log=lambda *a: None)
        self.assertEqual(r["erros"], [])
        # Quadro 3 (fixture) + 3 PAs × 9 + 11 zonas × 2 (nota a do 3A) + 7 alterações da Lei 18.081
        # + 1 manual (CA máx. 4,0 da ZEM no Arco Tietê)
        self.assertEqual(r["novo"], 70 + 27 + 22 + 7 + 1)
        zem = {x["parametro"]: x for x in base.vigente(self.con, "São Paulo", "ZEM")}
        self.assertEqual(zem["ca_max"]["valor"], 2.0)
        self.assertEqual(zem["ca_max"]["norma_id"], "LEI_16402_2016")
        self.assertEqual(zem["ca_max"]["fonte"], Q3_URL)
        self.assertTrue(any("6 m do perfil" in g["texto"] for g in base.regras(self.con, "São Paulo", "ZEM")))
        # rodar de novo não duplica
        with self._sp_sem_quadro4():
            r2 = consolidar.atualizar(self.con, "sao-paulo", origem_local=locais, log=lambda *a: None)
        self.assertEqual(r2["novo"] + r2["alterado"], 0)

    def test_alteracao_versiona(self):
        kw = dict(norma_id="L1", dispositivo="Q3", fonte="x", origem="extrator")
        base.gravar_parametro(self.con, "C", "Z1", "ca_max", 2.0, vigente_desde="2016-01-01", **kw)
        self.assertEqual(base.gravar_parametro(self.con, "C", "Z1", "ca_max", 2.5, norma_id="L2", dispositivo="art 5",
                                               fonte="y", origem="alteracao", vigente_desde="2024-01-19"), "alterado")
        self.assertEqual(base.gravar_parametro(self.con, "C", "Z1", "ca_max", 1.0, norma_id="L0", dispositivo="?",
                                               fonte="z", origem="alteracao", vigente_desde="2010-01-01"), "mais_antiga")
        h = base.historico(self.con, "C", "Z1", "ca_max")
        self.assertEqual([(x["valor"], x["vigente_ate"]) for x in h], [(2.0, "2024-01-19"), (2.5, None)])

    def test_natal_manual_sem_rede(self):
        r = consolidar.atualizar(self.con, "natal", log=lambda *a: None)
        self.assertEqual(r["erros"], [])
        g = {x["parametro"]: x for x in base.vigente(self.con, "Natal", "Município (geral)")}
        self.assertEqual(g["ca_bas"]["valor"], 1.0)
        self.assertEqual(g["gabarito_m"]["valor"], 140)
        self.assertEqual(g["ca_bas"]["conferido"], 0)


class TestVigia(unittest.TestCase):
    def test_avisos_sem_duplicar(self):
        tmp = tempfile.TemporaryDirectory()
        con = base.abrir(str(Path(tmp.name) / "t.db"))
        falso = lambda ibge, termo, desde: {"gazettes": [{"date": "2024-07-26", "url": "https://dom/1",
                                                           "excerpts": [f"... altera a {termo} ..."]}]}
        n1 = vigia.vigiar(con, "sao-paulo", "2024-01-01", consulta=falso, log=lambda *a: None)
        n2 = vigia.vigiar(con, "sao-paulo", "2024-01-01", consulta=falso, log=lambda *a: None)
        self.assertGreater(n1, 0)
        self.assertEqual(n2, 0)
        self.assertEqual(vigia.termos_da_norma({"numero": "16402"})[0], "Lei 16.402")
        con.close()
        tmp.cleanup()


class TestConfigs(unittest.TestCase):
    def test_configs_validas(self):
        for slug in consolidar.cidades_disponiveis():
            cfg = consolidar.carregar_cidade(slug)
            ids = {n["id"] for n in cfg["normas"]}
            for item in cfg.get("quadros", []) + cfg.get("manuais", []) + cfg.get("alteracoes", []):
                self.assertIn(item["norma"], ids, f"{slug}: norma desconhecida {item['norma']}")
                for p in ([item["parametro"]] if "parametro" in item else []):
                    self.assertTrue(extrator.parametro_valido(p), f"{slug}: parâmetro desconhecido {p}")


if __name__ == "__main__":
    unittest.main()


class TestManaus(unittest.TestCase):
    def test_setores_arcgis(self):
        linhas = extrator.manaus_setores((FIX / "manaus_setores.json").read_text(encoding="utf-8"))
        v = {(l["zona"], l["parametro"]): l["valor"] for l in linhas}
        self.assertEqual(v[("Setor 05", "ca_max")], 3.0)
        self.assertEqual(v[("Setor 05", "ca_bas")], 2.0)
        self.assertEqual(v[("Setor 05", "gabarito_pav")], 8.0)
        self.assertEqual(v[("Setor 05", "lote_min_m2")], 250.0)
        self.assertEqual(v[("Subsetor Orla Oeste", "ca_max")], 4.8)
        self.assertNotIn(("Subsetor Orla Oeste", "ca_bas"), v)
        self.assertFalse(any(z.startswith("Setor None") for z, _ in v))


class TestSPQuadros3Ae4(unittest.TestCase):
    def test_quadro3a(self):
        linhas = extrator.sp_quadro3a((FIX / "sp_quadro3a.txt").read_text(encoding="utf-8"))
        v = {(l["zona"], l["parametro"]): l for l in linhas}
        self.assertEqual(v[("PA 1", "permeabilidade_ate_500")]["valor"], 0.15)
        self.assertEqual(v[("PA 10", "qa_min_acima_10000")]["valor"], 0.42)
        self.assertIsNone(v[("PA 13", "fator_alfa_vegetacao")]["valor"])
        self.assertIn("Macroáreas", v[("PA 13", "fator_alfa_vegetacao")]["nota"])
        # nota (a): permeabilidade fixa por zona, grupos expandidos
        self.assertEqual(v[("ZEPAM", "permeabilidade_acima_500")]["valor"], 0.9)
        self.assertEqual(v[("ZPDSr", "permeabilidade_ate_500")]["valor"], 0.7)
        self.assertEqual(v[("ZCORa", "permeabilidade_ate_500")]["valor"], 0.3)
        self.assertEqual(v[("ZER-2", "permeabilidade_ate_500")]["valor"], 0.3)

    def test_quadro4_tabela(self):
        # cabeçalho como o pdfplumber devolve: grupo em cima, subzonas embaixo, "ZC-"+"ZEIS" partido,
        # zona só numa linha de cima ("ZPR") e células de dados deslocadas em relação ao cabeçalho
        t = [
            [None, "GRUPOS", "ZPR", "ZM", None, None, None, None, None],
            [None, None, None, "ZM", None, "ZMa", None, "ZC-", None],
            [None, None, None, None, None, None, None, "ZEIS", None],
            [None, "R1", "SIM", None, "SIM", None, "SIM", None, "NÃO (h)"],
            [None, "nR1-2", "NÃO", "SIM", None, "NÃO", None, None, "SIM"],
        ]
        out, falhas = extrator.sp_quadro4_tabelas([(2, t)])
        self.assertEqual(falhas, [])
        v = {(o["zona"], o["uso"]): o for o in out}
        self.assertEqual(sorted({z for z, _ in v}), ["ZC-ZEIS", "ZM", "ZMa", "ZPR"])
        self.assertTrue(v[("ZPR", "R1")]["permitido"])
        self.assertFalse(v[("ZC-ZEIS", "R1")]["permitido"])
        self.assertEqual(v[("ZC-ZEIS", "R1")]["nota"], "h")
        self.assertFalse(v[("ZMa", "nR1-2")]["permitido"])

    def test_quadro4_linha_desalinhada_e_falha(self):
        t = [[None, "G", "ZM", "ZMa"], [None, "R1", "SIM", None]]
        out, falhas = extrator.sp_quadro4_tabelas([(2, t)])
        self.assertEqual(out, [])
        self.assertEqual(falhas, [(2, "R1", 1, 2)])


class TestNatalAnexo2(unittest.TestCase):
    def test_leitor(self):
        texto = ("# comentário\n1.1;TIROL;MAX=5\n1.2;TIROL;BS=2,CS=5,GS=1.5,MAX=5\n"
                 "1.4;AVENIDA BAHIA;AN=3,BN=1.5\n")
        v = {(l["zona"]): l for l in extrator.natal_anexo2(texto)}
        self.assertEqual(v["Bairro TIROL"]["valor"], 5.0)
        self.assertEqual(v["Bairro TIROL"]["dispositivo"], "Anexo II, Quadro 1.2")  # prefere o quadro com bacias
        self.assertEqual(v["Bairro TIROL / Bacia GS"]["valor"], 1.5)
        self.assertEqual(v["Eixo AVENIDA BAHIA / Bacia AN"]["valor"], 3.0)
        with self.assertRaises(ValueError):
            extrator.natal_anexo2("1.2;X;AS=4,MAX=5\n")  # MAX diferente do maior CA das bacias

    def test_transcricao_bate_com_ca_das_bacias(self):
        import math
        cfg = consolidar.carregar_cidade("natal")
        bacia = {m["zona"][6:]: m["valor"] for m in cfg["manuais"] if m["zona"].startswith("Bacia ")}
        linhas = extrator.natal_anexo2((consolidar.PASTA_CIDADES / "natal_anexo2.txt").read_text(encoding="utf-8"))
        self.assertEqual(len(linhas), 244)
        for l in linhas:
            if "/ Bacia " not in l["zona"]:
                continue
            b = bacia[l["zona"].split("/ Bacia ")[1]]
            if l["zona"].startswith("Bairro"):
                self.assertEqual(l["valor"], b, l["zona"])
            else:  # eixo: +50% sobre a bacia (art. 15 §4º), em passos de 0,5, teto 5,0
                self.assertEqual(l["valor"], min(5.0, math.ceil(b * 3) / 2), l["zona"])
