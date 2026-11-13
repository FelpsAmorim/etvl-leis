"""Testes offline da zona do lote (GeoSampa simulado)."""

import json
import re
import unittest
from pathlib import Path

from evtl_leis import lote

FIX = json.loads((Path(__file__).parent / "fixtures" / "geosampa_lote.json").read_text(encoding="utf-8"))


def feat(cod, nome):
    return {"properties": {"cd_zoneamento_perimetro": cod, "tx_zoneamento_perimetro": nome}}


def falso_wfs(camada, cql, campos=None, limite=20):
    """Lote 20×10 m: 75% em ZM e 25% em ZEU no mapa de 2024; 100% ZEU em 2016."""
    if camada == lote.CAMADA_LOTE:
        return FIX["lote"]
    m = re.match(r"INTERSECTS\(ge_poligono, POINT\(([\d.]+) ([\d.]+)\)\)", cql)
    if camada == lote.CAMADAS_ZONA["2016"]["camada"]:
        return [feat("ZEU", "ZEU")]
    if m:
        x, y = float(m.group(1)), float(m.group(2))
        if lote.contem(FIX["zona_2024"], x, y):
            return [feat("ZM", "Zona Mista")]
        return [feat("ZEU", "Zona Eixo")]
    return [feat("ZM", "Zona Mista"), feat("ZEU", "Zona Eixo")]


class TestLote(unittest.TestCase):
    def test_sql(self):
        self.assertEqual(lote.ler_sql("009.001.0074-1"), ("009", "001", "0074", "1"))
        self.assertEqual(lote.ler_sql("0090010074"), ("009", "001", "0074", None))
        with self.assertRaises(ValueError):
            lote.ler_sql("9.1.74")

    def test_logradouro(self):
        self.assertEqual(lote.normalizar_logradouro("Avenida Paulista"), "PAULISTA")
        self.assertEqual(lote.normalizar_logradouro("R. Samuel das Neves"), "SAMUEL DAS NEVES")
        self.assertEqual(lote.normalizar_logradouro("Praça João Mendes"), "JOAO MENDES")

    def test_utm(self):
        # vértice do lote 010.059.0007 (R. Augusta 1403): GeoSampa em EPSG:31983 e reprojetado para 4326
        x, y = lote.latlon_para_utm23s(-23.55636903, -46.65759754)
        self.assertAlmostEqual(x, 330814.89, delta=1)
        self.assertAlmostEqual(y, 7393909.92, delta=1)

    def test_geometria(self):
        g = FIX["lote"][0]["geometry"]
        self.assertTrue(lote.contem(g, 5, 5))
        self.assertFalse(lote.contem(g, 25, 5))
        self.assertEqual(len(lote.amostras(g, n=4)), 16)
        self.assertTrue(lote.wkt(g).startswith("POLYGON((0.000 0.000"))

    def test_zona_dividida_e_mudanca(self):
        r = lote.zona(sql="009.001.0000", get=falso_wfs)
        self.assertEqual(r["lote"]["endereco"], "R SAMUEL DAS NEVES 74")
        self.assertEqual(r["zona"], "ZM")
        fr = {z["zona"]: z["fracao"] for z in r["zonas_2024"]}
        self.assertAlmostEqual(fr["ZM"], 0.67, delta=0.1)
        self.assertAlmostEqual(fr["ZEU"], 0.33, delta=0.1)
        self.assertEqual(r["zonas_2016"][0]["zona"], "ZEU")
        self.assertTrue(r["mudou_desde_2016"])
        self.assertEqual(r["fonte_2024"]["norma_id"], "LEI_18177_2024")
        self.assertIn("perimetro_zona_lei_18177_24", r["fonte_2024"]["camada"])

    def test_cql_endereco_escapa_aspas(self):
        vistos = []
        lote.achar_lote(endereco="Rua D'Ávila", numero="10",
                        get=lambda c, q, campos=None: vistos.append(q) or [])
        self.assertIn("D AVILA", vistos[0])
        self.assertIn("cd_numero_porta='10'", vistos[0])
