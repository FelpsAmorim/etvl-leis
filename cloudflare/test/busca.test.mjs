// Testes offline da rota /buscar (node --test). As fontes externas são simuladas e o D1 é
// trocado por um SQLite em memória carregado com schema.sql + d1_dados.sql (a base real).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { buscar, aplicaveis, latlonParaUtm23s, lerSql, normalizarLogradouro, ErroEntrada, ErroFonte } from "../src/busca.js";
import worker from "../src/index.js";

const raiz = new URL("../", import.meta.url);
const sqlite = new DatabaseSync(":memory:");
sqlite.exec(readFileSync(new URL("schema.sql", raiz), "utf8"));
sqlite.exec(readFileSync(new URL("d1_dados.sql", raiz), "utf8"));

// D1 mínimo: prepare().bind().all() -> { results }
const db = {
  prepare(sql) {
    let args = [];
    const st = {
      bind: (...a) => ((args = a), st),
      all: async () => ({ results: sqlite.prepare(sql).all(...args).map((r) => ({ ...r })) }),
    };
    return st;
  },
};

// ---- fontes simuladas ----
const params = (u) => Object.fromEntries(new URL(u).searchParams);

function falsoArcgis(u) {
  const p = params(u);
  if (u.includes("findAddressCandidates"))
    return { candidates: [{ address: "1661 AVENIDA DJALMA BATISTA, CHAPADA", score: 100, location: { x: -60.0242, y: -3.1009 } }] };
  if (u.includes("/MapServer/3/query")) {
    if (p.where)
      return { features: [{ attributes: { CGL: "1600280691", SETOR: "16", QUADRA: "0028", LOTE: "0691" },
        geometry: { rings: [[[-60.03, -3.1], [-60.02, -3.1], [-60.02, -3.11], [-60.03, -3.11]]] } }] };
    if (!p.distance) return { features: [] }; // endereço caiu na rua
    return { features: [{ attributes: { CGL: "1600280691", SETOR: "16", QUADRA: "0028", LOTE: "0691" } }] };
  }
  if (u.includes("/MapServer/5/query")) return { features: [{ attributes: { SETOR: "13", BAIRRO: "CHAPADA" } }] };
  if (u.includes("/MapServer/4/query")) return { features: [{ attributes: { SETOR_: "SUBSETOR ORLA OESTE" } }] };
  if (u.includes("/MapServer/6/query")) return { features: [{ attributes: { NOME_BAIRR: "CHAPADA" } }] };
  if (u.includes("/Bairros/FeatureServer")) return { features: [{ attributes: { bairro: "Tirol", reg_adm: "Leste" } }] };
  if (u.includes("/ZA_revetorizada/") || u.includes("/AEIS/")) return { features: [{ attributes: { objectid: 1 } }] };
  return { features: [] };
}

// Lote quadrado de 20 m em SP, metade em ZM e metade em ZEU (divisa em x = 10)
const LOTE = { type: "Polygon", coordinates: [[[0, 0], [20, 0], [20, 20], [0, 20], [0, 0]]] };
function falsoGeosampa(u) {
  const p = params(u);
  const camada = p.typeNames.replace("geoportal:", "");
  if (camada === "lote_cidadao")
    return { features: [{ properties: { cd_setor_fiscal: "009", cd_quadra_fiscal: "096", cd_lote: "0000", cd_digito_sql: "1",
      tx_tipo_lote: "FISCAL", nm_logradouro_completo: "R SAMUEL DAS NEVES", cd_numero_porta: "74", qt_area_terreno: 400 }, geometry: LOTE }] };
  const z = (cod) => ({ properties: { cd_zoneamento_perimetro: cod, tx_zoneamento_perimetro: cod } });
  const m = /POINT\(([\d.-]+) ([\d.-]+)\)/.exec(p.CQL_FILTER);
  if (camada === "perimetro_zona_lei_18177_24") {
    if (m) return { features: [z(Number(m[1]) < 10 ? "ZM" : "ZEU")] };
    return { features: [z("ZM"), z("ZEU")] };
  }
  if (camada === "pde_macroarea_lei_18209") return { features: [{ properties: { nm: "Macroárea de Urbanização Consolidada" } }] };
  return { features: [z("ZEU")] }; // 2016: tudo ZEU
}

// Lote inteiro em ZEM; macro = sigla da macroárea (null: camada fora do ar). Atributos da sondagem de 17/11/2026.
const geosampaZem = (macro, { tombado = false } = {}) => (u) => {
  const { typeNames, CQL_FILTER } = params(u);
  const camada = typeNames.replace("geoportal:", "");
  if (camada === "lote_cidadao") return falsoGeosampa(u);
  if (camada === "pde_macroarea_lei_18209") {
    if (macro === null) throw new ErroFonte("fora do ar");
    return { features: [{ properties: { sg_macroarea: macro, nm_macroarea: "Macroarea " + macro } }] };
  }
  if (camada === "patrimonio_cultural_area_envoltoria_CONDEPHAAT")
    return { features: [{ properties: { nm_area: "A.E PALÁCIO DOS CAMPOS ELÍSEOS_300 metros", tx_resolucao_condephaat: "RES. SC S/N/1977" } }] };
  if (camada === "perimetro_aiu") return { features: [{ properties: { nm_lei: "AIU Setor Central", cd_numero_lei: "17844/22" } }] };
  if (camada === "patrimonio_cultural_bem_tombado") {
    assert.match(CQL_FILTER, /cd_lote='0000'/); // pelo SQL do lote, não por encostar na divisa
    return { features: tombado ? [{ properties: { nm_area_tombada: "BAIRRO DOS CAMPOS ELÍSEOS", tx_resolucao_conpresp: "RES. 03/18",
      tx_resolucao_condephaat: "RES. SC 20/13" } }] : [] };
  }
  if (camada.startsWith("perimetro_zona") || camada.startsWith("zoneamento_2016"))
    return { features: [{ properties: { cd_zoneamento_perimetro: "ZEM", tx_zoneamento_perimetro: "ZEM" } }] };
  return { features: [] };
};

// ---- Manaus ----
test("Manaus por endereço: geocodifica, cai na rua e acha o lote a 20 m", async () => {
  const r = await buscar(db, { cidade: "manaus", endereco: "Av Djalma Batista, 1661" }, falsoArcgis);
  assert.deepEqual(r.zonas, ["Setor 13", "Subsetor Orla Oeste"]);
  assert.equal(r.lote.cgl, "1600280691");
  assert.equal(r.bairro, "CHAPADA");
  assert.ok(r.parametros["Setor 13"].length > 0, "Setor 13 tem parâmetros na base");
});

test("Manaus por CGL usa o centro do lote", async () => {
  const r = await buscar(db, { cidade: "manaus", cgl: "1600280691" }, falsoArcgis);
  assert.ok(Math.abs(r.ponto.lon - -60.025) < 0.001);
  assert.equal(r.zonas[0], "Setor 13");
});

// ---- Natal ----
test("Natal com bairro, bacia e eixo informados (igual ao Python)", async () => {
  const r = await buscar(db, { cidade: "natal", bairro: "tirol", bacia: "cs", eixo: "prudente de morais" }, falsoArcgis);
  assert.deepEqual(r.zonas, ["Bairro TIROL", "Bacia CS", "Bairro TIROL / Bacia CS",
    "Eixo AVENIDA PRUDENTE DE MORAIS / Bacia CS", "Município (geral)"]);
  for (const z of r.zonas) assert.ok(r.parametros[z].length > 0, `${z} sem parâmetros`);
  assert.ok(r.regras.length > 0);
  assert.match(r.aviso_conferencia, /conferido=0/);
});

test("Natal por coordenada: bairro oficial e sobreposições", async () => {
  const r = await buscar(db, { cidade: "Natal", lat: "-5.80", lon: "-35.20" }, falsoArcgis);
  assert.equal(r.bairro_oficial, "Tirol");
  assert.deepEqual(r.sobreposicoes, ["Zona Adensável", "Área Especial de Interesse Social"]);
  assert.equal(r.zonas[0], "Bairro TIROL");
  assert.ok(r.bacias_do_bairro.length > 0);
  assert.ok(r.avisos.some((a) => a.includes("Área Especial de Interesse Social")));
});

// ---- São Paulo ----
test("SP por SQL: lote em duas zonas com fração estimada e mudança desde 2016", async () => {
  const r = await buscar(db, { cidade: "sao-paulo", sql: "009.096.0000" }, falsoGeosampa);
  assert.equal(r.lote.sql, "009.096.0000-1");
  // grade 5x5: colunas em x = 2, 6 (ZM) e 10, 14, 18 (ZEU)
  assert.deepEqual(r.zonas_2024.map((z) => [z.zona, z.fracao]), [["ZEU", 0.6], ["ZM", 0.4]]);
  assert.deepEqual(r.zonas, ["ZEU", "ZM"]);
  assert.equal(r.mudou_desde_2016, true);
  assert.ok(r.parametros.ZM.length > 0 && r.parametros.ZEU.length > 0);
});

test("data de referência devolve a versão antiga (ZPI-2, TO antes da Lei 18.081)", async () => {
  const to = (ps) => ps.find((p) => p.parametro === "to_acima_500").valor;
  assert.equal(to(await buscarZona("ZPI-2", "2020-01-01")), 0.3);
  assert.equal(to(await buscarZona("ZPI-2")), 0.5);
});

async function buscarZona(zona, data) {
  const res = await worker.fetch(new Request(`https://x/consultar?cidade=sao-paulo&zona=${zona}${data ? "&data=" + data : ""}`), { DB: db });
  return (await res.json()).parametros;
}

// ---- entradas ----
test("erros de entrada viram 400 na rota", async () => {
  for (const q of ["cidade=curitiba&lat=1&lon=1", "cidade=natal&lat=1", "cidade=natal&bairro=Tirol&data=ontem", "cidade=sao-paulo"]) {
    const res = await worker.fetch(new Request(`https://x/buscar?${q}`), { DB: db });
    assert.equal(res.status, 400, q);
  }
});

test("utilitários batem com o Python", () => {
  assert.deepEqual(lerSql("009.096.0000-1"), ["009", "096", "0000", "1"]);
  assert.throws(() => lerSql("9.96"), ErroEntrada);
  assert.equal(normalizarLogradouro("Rua Samuel das Neves"), "SAMUEL DAS NEVES");
  const [x, y] = latlonParaUtm23s(-23.5505, -46.6333); // Praça da Sé
  assert.ok(Math.abs(x - 333_400) < 1_000 && Math.abs(y - 7_394_500) < 1_000, `${x} ${y}`);
});

// ---- precedência e Arco Tietê (comparação com os laudos da Legali, 17/11/2026) ----
test("SP: lote na MEM pede confirmação do Arco Tietê e fica com CA 2,0", async () => {
  const r = await buscar(db, { cidade: "sao-paulo", sql: "008.018.0069-2" }, geosampaZem("MEM"));
  assert.equal(r.arco_tiete, null);
  assert.equal(r.aplicaveis.ca_max.valor, 2.0);
  assert.ok(r.avisos.some((a) => a.includes("MEM") && a.includes("arco_tiete=sim")));
});

test("SP: ZEM no Arco Tietê confirmado tem CA máx. 4,0 (Porto Seguro)", async () => {
  const r = await buscar(db, { cidade: "sao-paulo", sql: "008.018.0069-2", arco_tiete: "sim" }, geosampaZem("MEM"));
  assert.deepEqual(r.zonas, ["ZEM", "ZEM / Arco Tietê"]);
  assert.equal(r.aplicaveis.ca_max.valor, 4.0);
  assert.deepEqual(r.aplicaveis.ca_max.substitui, [{ zona: "ZEM", valor: 2.0 }]);
  assert.equal(r.aplicaveis.to_acima_500.valor, 0.7);
  assert.ok(r.regras.some((g) => g.norma_id === "RES_CTLU_004_2018"));
});

test("SP: fora da MEM não é Arco Tietê", async () => {
  const r = await buscar(db, { cidade: "sao-paulo", sql: "008.018.0069-2" }, geosampaZem("MQU"));
  assert.equal(r.arco_tiete, false);
  assert.equal(r.aplicaveis.ca_max.valor, 2.0);
  assert.ok(!(r.avisos || []).some((a) => a.includes("arco_tiete")));
});

test("SP: camada de macroáreas fora do ar avisa", async () => {
  const r = await buscar(db, { cidade: "sao-paulo", sql: "008.018.0069-2" }, geosampaZem(null));
  assert.equal(r.arco_tiete, null);
  assert.ok(r.avisos.some((a) => a.includes("arco_tiete=sim")));
});

test("SP: sobreposições de patrimônio, AIU e tombamento", async () => {
  const r = await buscar(db, { cidade: "sao-paulo", sql: "008.018.0069-2" }, geosampaZem("MEM", { tombado: true }));
  assert.deepEqual(r.sobreposicoes.map((x) => x.tipo), ["envoltoria_condephaat", "aiu", "tombado"]);
  assert.ok(r.avisos.some((a) => a.startsWith("A.E PALÁCIO DOS CAMPOS ELÍSEOS")));
  assert.ok(r.avisos.some((a) => a.includes("BAIRRO DOS CAMPOS ELÍSEOS") && a.includes("RES. SC 20/13")));
});

test("Manaus: 'Sete de Setembro' vira '7 de Setembro' no geocodificador", async () => {
  const pedidos = [];
  const get = (u) => {
    if (u.includes("findAddressCandidates")) {
      const t = params(u).SingleLine;
      pedidos.push(t);
      return t.includes("7 DE SETEMBRO")
        ? { candidates: [{ address: "895 AVENIDA 7 DE SETEMBRO, CENTRO, MANAUS", score: 90.22, location: { x: -60.0223, y: -3.1346 } }] }
        : { candidates: [] };
    }
    return falsoArcgis(u);
  };
  const r = await buscar(db, { cidade: "manaus", endereco: "Avenida Sete de Setembro, 895" }, get);
  assert.deepEqual(pedidos, ["Avenida Sete de Setembro, 895", "AVENIDA 7 DE SETEMBRO, 895"]);
  assert.equal(r.endereco_encontrado, "895 AVENIDA 7 DE SETEMBRO, CENTRO, MANAUS");
});

test("SP: lote em duas zonas não mistura parâmetros", async () => {
  const r = await buscar(db, { cidade: "sao-paulo", sql: "009.096.0000" }, falsoGeosampa);
  assert.equal(r.aplicaveis, null);
});

test("Manaus: subsetor prevalece sobre o setor (AG 0230, Sítio Histórico)", () => {
  const p = (parametro, valor) => ({ parametro, valor, norma_id: "N", dispositivo: "d", nota: null, conferido: 0 });
  const a = aplicaveis("manaus", ["Setor 01", "Subsetor Sítio Histórico"], {
    "Setor 01": [p("ca_bas", 2.0), p("ca_max", 4.0), p("gabarito_pav", 16.0)],
    "Subsetor Sítio Histórico": [p("ca_max", 2.0), p("gabarito_pav", 3.0)],
  });
  assert.equal(a.ca_max.valor, 2.0);
  assert.equal(a.gabarito_pav.valor, 3.0);
  assert.equal(a.ca_bas.herdado, true);
});

test("Natal: eixo prevalece (SmartFit, CA aplicável 4,0)", async () => {
  const r = await buscar(db, { cidade: "natal", bairro: "capim macio", bacia: "ms", eixo: "roberto freire" }, falsoArcgis);
  assert.equal(r.aplicaveis.ca_max.valor, 4.0);
  assert.equal(r.aplicaveis.to_acima_500.valor, 0.8);
  assert.equal(r.aplicaveis.gabarito_m.valor, 140);
});

test("arco_tiete inválido vira 400", async () => {
  const res = await worker.fetch(new Request("https://x/buscar?cidade=sao-paulo&sql=008.018.0069-2&arco_tiete=talvez"), { DB: db });
  assert.equal(res.status, 400);
});
