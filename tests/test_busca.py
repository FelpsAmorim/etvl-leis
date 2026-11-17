"""Testes offline da busca do lote (serviços ArcGIS simulados)."""

import tempfile
import unittest
from pathlib import Path

from evtl_leis import base, busca, consolidar


def falso_arcgis(url, params):
    if url.endswith("findAddressCandidates"):
        return {"candidates": [{"address": "1661 AVENIDA DJALMA BATISTA, CHAPADA", "score": 100,
                                "location": {"x": -60.0242, "y": -3.1009}}]}
    if "/MapServer/3/query" in url:
        if "where" in params:
            return {"features": [{"attributes": {"CGL": "1600280691", "SETOR": "16", "QUADRA": "0028", "LOTE": "0691"},
                                  "geometry": {"rings": [[[-60.03, -3.10], [-60.02, -3.10], [-60.02, -3.11], [-60.03, -3.11]]]}}]}
        if "distance" not in params:
            return {"features": []}  # endereço caiu na rua
        return {"features": [{"attributes": {"CGL": "1600280691", "SETOR": "16", "QUADRA": "0028", "LOTE": "0691"}}]}
    if "/MapServer/5/query" in url:
        return {"features": [{"attributes": {"SETOR": "13", "BAIRRO": "CHAPADA"}}]}
    if "/MapServer/4/query" in url:
        return {"features": [{"attributes": {"SETOR_": "SUBSETOR ORLA OESTE"}}]}
    if "/MapServer/6/query" in url:
        return {"features": [{"attributes": {"NOME_BAIRR": "CHAPADA"}}]}
    if "/Bairros/FeatureServer" in url:
        return {"features": [{"attributes": {"bairro": "Tirol", "reg_adm": "Leste"}}]}
    if "/ZA_revetorizada/" in url or "/AEIS/" in url:
        return {"features": [{"attributes": {"objectid": 1}}]}
    return {"features": []}


class TestManaus(unittest.TestCase):
    def test_endereco(self):
        r = busca.manaus(endereco="Av Djalma Batista, 1661", get=falso_arcgis)
        self.assertEqual(r["zonas"], ["Setor 13", "Subsetor Orla Oeste"])
        self.assertEqual(r["lote"]["cgl"], "1600280691")
        self.assertEqual(r["bairro"], "CHAPADA")

    def test_cgl_usa_centro_do_lote(self):
        r = busca.manaus(cgl="1600280691", get=falso_arcgis)
        self.assertAlmostEqual(r["ponto"]["lon"], -60.025, places=3)
        self.assertEqual(r["zonas"][0], "Setor 13")


class TestNatal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = base.abrir(str(Path(self.tmp.name) / "t.db"))
        consolidar.atualizar(self.con, "natal", log=lambda *a: None)  # tudo local (manuais + natal_anexo2.txt)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_bairro_bacia_eixo_informados(self):
        r = busca.natal(self.con, bairro="tirol", bacia="cs", eixo="prudente de morais")
        self.assertEqual(r["zonas"], ["Bairro TIROL", "Bacia CS", "Bairro TIROL / Bacia CS",
                                      "Eixo AVENIDA PRUDENTE DE MORAIS / Bacia CS", "Município (geral)"])
        self.assertEqual(r["avisos"], [])

    def test_bairro_sem_bacia_lista_opcoes(self):
        r = busca.natal(self.con, bairro="Ponta Negra")
        self.assertEqual(r["bacias_do_bairro"], ["JS", "MS", "NS"])
        self.assertTrue(any("Informe a bacia" in a for a in r["avisos"]))

    def test_bacia_que_nao_passa_no_bairro(self):
        r = busca.natal(self.con, bairro="Tirol", bacia="QN")
        self.assertTrue(any("não passa" in a for a in r["avisos"]))

    def test_ponto_usa_bairro_oficial_e_sobreposicoes(self):
        r = busca.natal(self.con, lat=-5.81, lon=-35.20, get=falso_arcgis)
        self.assertEqual(r["bairro_oficial"], "Tirol")
        self.assertIn("Bairro TIROL", r["zonas"])
        self.assertEqual(r["sobreposicoes"], ["Zona Adensável", "Área Especial de Interesse Social"])
        self.assertTrue(any("Área Especial de Interesse Social" in a for a in r["avisos"]))

    def test_buscar_junta_parametros_e_regras(self):
        r = busca.buscar(self.con, "natal", bairro="Tirol", bacia="CS")
        self.assertEqual(r["parametros"]["Bairro TIROL / Bacia CS"][0]["valor"], 5.0)
        self.assertTrue(any("eixos estruturantes" in g["texto"] for g in r["regras"]))


class TestDataDeReferencia(unittest.TestCase):
    def test_vigente_na_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            con = base.abrir(str(Path(tmp) / "t.db"))
            kw = dict(norma_id="X", dispositivo="art. 1", fonte="teste", origem="manual")
            base.gravar_parametro(con, "São Paulo", "ZPI-2", "to_acima_500", 0.3, vigente_desde="2016-03-23", **kw)
            base.gravar_parametro(con, "São Paulo", "ZPI-2", "to_acima_500", 0.5, vigente_desde="2024-01-19", **kw)
            v = lambda d=None: [r["valor"] for r in base.vigente(con, "São Paulo", "ZPI-2", data=d)]
            self.assertEqual(v(), [0.5])
            self.assertEqual(v("2020-01-01"), [0.3])
            self.assertEqual(v("2024-01-19"), [0.5])
            self.assertEqual(v("2010-01-01"), [])
            con.close()
