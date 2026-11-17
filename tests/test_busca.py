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


class TestPrecedencia(unittest.TestCase):
    """Correções vindas da comparação com os laudos da Legali (17/11/2026)."""

    def _p(self, parametro, valor, norma="N"):
        return {"parametro": parametro, "valor": valor, "norma_id": norma, "dispositivo": "d", "nota": None, "conferido": 0}

    def test_manaus_subsetor_prevalece(self):
        # AG 0230: Sítio Histórico tem CA máx. 2 e 3 pavimentos; o Setor 01 tem 4,0 e 16
        ps = {"Setor 01": [self._p("ca_bas", 2.0), self._p("ca_max", 4.0), self._p("gabarito_pav", 16.0)],
              "Subsetor Sítio Histórico": [self._p("ca_max", 2.0), self._p("gabarito_pav", 3.0)]}
        a = busca.aplicaveis("manaus", ["Setor 01", "Subsetor Sítio Histórico"], ps)
        self.assertEqual(a["ca_max"]["valor"], 2.0)
        self.assertEqual(a["ca_max"]["zona"], "Subsetor Sítio Histórico")
        self.assertEqual(a["ca_max"]["substitui"], [{"zona": "Setor 01", "valor": 4.0}])
        self.assertEqual(a["gabarito_pav"]["valor"], 3.0)
        self.assertTrue(a["ca_bas"]["herdado"])
        self.assertNotIn("herdado", a["ca_max"])

    def test_natal_eixo_prevalece_sobre_bacia_e_bairro(self):
        ps = {"Bairro CAPIM MACIO": [self._p("ca_max", 5.0)], "Bacia MS": [self._p("ca_max", 2.5)],
              "Bairro CAPIM MACIO / Bacia MS": [self._p("ca_max", 2.5)],
              "Eixo AVENIDA ENGENHEIRO ROBERTO FREIRE / Bacia MS": [self._p("ca_max", 4.0)],
              "Município (geral)": [self._p("ca_bas", 1.0), self._p("to_acima_500", 0.8)]}
        a = busca.aplicaveis("natal", list(ps), ps)
        self.assertEqual(a["ca_max"]["valor"], 4.0)  # SmartFit: CA aplicável 4,0
        self.assertEqual(a["to_acima_500"]["valor"], 0.8)
        self.assertNotIn("herdado", a["to_acima_500"])

    def test_sp_lote_em_duas_zonas_nao_mistura(self):
        self.assertIsNone(busca.aplicaveis("sao-paulo", ["ZM", "ZEU"], {"ZM": [], "ZEU": []}))


class TestArcoTiete(unittest.TestCase):
    """Porto Seguro (jun/2026): ZEM no Subsetor Arco Tietê tem CA máx. 4,0 (Res. CTLU 004/2018)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = base.abrir(str(Path(self.tmp.name) / "t.db"))
        kw = dict(norma_id="LEI_16402_2016", dispositivo="Quadro 3", fonte="t", origem="extrator", vigente_desde="2016-03-23")
        base.gravar_parametro(self.con, "São Paulo", "ZEM", "ca_max", 2.0, **kw)
        base.gravar_parametro(self.con, "São Paulo", "ZEM", "to_acima_500", 0.7, **kw)
        cfg = consolidar.carregar_cidade("sao-paulo")
        for m in cfg["manuais"]:
            base.gravar_parametro(self.con, "São Paulo", m["zona"], m["parametro"], m["valor"], norma_id=m["norma"],
                                  dispositivo=m["dispositivo"], fonte=m["fonte"], vigente_desde=m["vigente_desde"],
                                  origem="manual")

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    @staticmethod
    def geosampa(macro, tombado=False):
        """macro: sigla da macroárea (None: camada fora do ar). Atributos copiados da sondagem de 17/11/2026."""
        quad = {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]}

        def get(camada, cql, campos=None, limite=20):
            if camada == "lote_cidadao":
                return [{"properties": {"cd_setor_fiscal": "008", "cd_quadra_fiscal": "018", "cd_lote": "0069",
                                        "tx_tipo_lote": "FISCAL"}, "geometry": quad}]
            if camada.startswith("perimetro_zona") or camada.startswith("zoneamento_2016"):
                return [{"properties": {"cd_zoneamento_perimetro": "ZEM", "tx_zoneamento_perimetro": "ZEM"}}]
            if camada == "pde_macroarea_lei_18209":
                if macro is None:
                    raise busca.ErroFonte("camada fora do ar")
                return [{"properties": {"sg_macroarea": macro, "nm_macroarea": "Macroarea " + macro}}]
            if camada == "patrimonio_cultural_area_envoltoria_CONDEPHAAT":
                return [{"properties": {"nm_area": "A.E PALÁCIO DOS CAMPOS ELÍSEOS_300 metros",
                                        "tx_resolucao_condephaat": "RES. SC S/N/1977"}}]
            if camada == "perimetro_aiu":
                return [{"properties": {"nm_lei": "AIU Setor Central", "cd_numero_lei": "17844/22"}}]
            if camada == "patrimonio_cultural_bem_tombado":
                assert "cd_lote='0069'" in cql  # o tombamento vai pelo SQL do lote, não por encostar
                return [{"properties": {"nm_area_tombada": "BAIRRO DOS CAMPOS ELÍSEOS",
                                        "tx_resolucao_conpresp": "RES. 03/18 - ex-officio",
                                        "tx_resolucao_condephaat": "RES. SC 20/13"}}] if tombado else []
            return []
        return get

    def test_na_mem_pede_confirmacao_do_subsetor(self):
        r = busca.buscar(self.con, "sao-paulo", sql="008.018.0069-2", get=self.geosampa("MEM"))
        self.assertIsNone(r["arco_tiete"])
        self.assertEqual(r["aplicaveis"]["ca_max"]["valor"], 2.0)
        self.assertTrue(any("arco_tiete=sim" in a and "MEM" in a for a in r["avisos"]))

    def test_na_mem_com_confirmacao(self):
        r = busca.buscar(self.con, "sao-paulo", sql="008.018.0069-2", get=self.geosampa("MEM"), arco_tiete=True)
        self.assertEqual(r["zonas"], ["ZEM", "ZEM / Arco Tietê"])
        self.assertEqual(r["aplicaveis"]["ca_max"]["valor"], 4.0)
        self.assertEqual(r["aplicaveis"]["ca_max"]["substitui"], [{"zona": "ZEM", "valor": 2.0}])
        self.assertEqual(r["aplicaveis"]["to_acima_500"]["valor"], 0.7)
        self.assertEqual(r["arco_tiete_origem"], "informado pelo usuário")

    def test_fora_da_mem_nao_e_arco_tiete(self):
        r = busca.buscar(self.con, "sao-paulo", sql="008.018.0069-2", get=self.geosampa("MQU"))
        self.assertFalse(r["arco_tiete"])
        self.assertEqual(r["aplicaveis"]["ca_max"]["valor"], 2.0)
        self.assertFalse(any("arco_tiete" in a for a in r.get("avisos", [])))

    def test_camada_fora_do_ar_avisa(self):
        r = busca.buscar(self.con, "sao-paulo", sql="008.018.0069-2", get=self.geosampa(None))
        self.assertIsNone(r["arco_tiete"])
        self.assertTrue(any("arco_tiete=sim" in a for a in r["avisos"]))

    def test_sobreposicoes(self):
        r = busca.buscar(self.con, "sao-paulo", sql="008.018.0069-2", get=self.geosampa("MEM", tombado=True))
        tipos = [s["tipo"] for s in r["sobreposicoes"]]
        self.assertEqual(tipos, ["envoltoria_condephaat", "aiu", "tombado"])
        self.assertTrue(any(a.startswith("A.E PALÁCIO DOS CAMPOS ELÍSEOS") for a in r["avisos"]))
        self.assertTrue(any("BAIRRO DOS CAMPOS ELÍSEOS" in a and "RES. SC 20/13" in a for a in r["avisos"]))


class TestManausEndereco(unittest.TestCase):
    def test_numero_por_extenso_vira_algarismo(self):
        # sondagem 17/11/2026: 'Sete de Setembro' não acha; 'AV. 7 DE SETEMBRO, 895' acha com score 90
        pedidos = []

        def get(url, params):
            pedidos.append(params["SingleLine"])
            if "7 DE SETEMBRO" in params["SingleLine"]:
                return {"candidates": [{"address": "895 AVENIDA 7 DE SETEMBRO, CENTRO, MANAUS", "score": 90.22,
                                        "location": {"x": -60.0223, "y": -3.1346}}]}
            return {"candidates": []}
        lat, lon, end = busca.geocodificar_manaus("Avenida Sete de Setembro, 895", get)
        self.assertEqual(pedidos, ["Avenida Sete de Setembro, 895", "AVENIDA 7 DE SETEMBRO, 895"])
        self.assertAlmostEqual(lat, -3.1346)

    def test_sem_data_no_nome_nao_inventa_variante(self):
        self.assertEqual(busca.variantes_endereco("Av Djalma Batista, 1661"), ["Av Djalma Batista, 1661"])
