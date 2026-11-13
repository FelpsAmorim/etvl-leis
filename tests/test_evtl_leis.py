"""Testes offline: rodam sem rede. python -m unittest discover -s tests"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from evtl_leis import base, consolidar, extrator, fontes, vigia

FIX = Path(__file__).parent / "fixtures"
Q3_URL = "https://gestaourbana.prefeitura.sp.gov.br/wp-content/uploads/2016/03/005-QUADRO_3_FINAL.pdf"


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

    def test_sp_offline(self):
        r = consolidar.atualizar(self.con, "sao-paulo", origem_local={Q3_URL: str(FIX / "sp_quadro3.txt")}, log=lambda *a: None)
        self.assertEqual(r["erros"], [])
        self.assertEqual(r["novo"], 70)
        zem = {x["parametro"]: x for x in base.vigente(self.con, "São Paulo", "ZEM")}
        self.assertEqual(zem["ca_max"]["valor"], 2.0)
        self.assertEqual(zem["ca_max"]["norma_id"], "LEI_16402_2016")
        self.assertEqual(zem["ca_max"]["fonte"], Q3_URL)
        self.assertTrue(any("6 m do perfil" in g["texto"] for g in base.regras(self.con, "São Paulo", "ZEM")))
        # rodar de novo não duplica
        r2 = consolidar.atualizar(self.con, "sao-paulo", origem_local={Q3_URL: str(FIX / "sp_quadro3.txt")}, log=lambda *a: None)
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
                    self.assertIn(p, extrator.PARAMETROS)


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
