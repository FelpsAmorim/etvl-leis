// Busca do lote no Worker: endereço/coordenada/inscrição -> zona(s) -> parâmetros e regras do D1.
// Mesma lógica de evtl_leis/busca.py e evtl_leis/lote.py (a referência é o Python; mantenha os dois iguais).
//
// Limite do plano free: 50 subrequests por chamada. Pior caso aqui: SP com lote em várias zonas
// (1 lote + 1 zona + até 25 amostras + 1 de 2016 + 1 macroárea + 8 sobreposições = 37); Natal com coordenada = 18;
// Manaus = até 8 (o endereço pode ir em duas tentativas, mais a busca de subsetor a 30 m).

const UA = { "User-Agent": "evtl-leis/0.2 (+consulta de legislacao urbanistica)" };

export class ErroFonte extends Error {}
export class ErroEntrada extends Error {}

// get(url) -> JSON. Trocável nos testes.
export async function getJson(url) {
  let r;
  try {
    r = await fetch(url, { headers: UA, signal: AbortSignal.timeout(25000) });
  } catch (e) {
    throw new ErroFonte(`${new URL(url).host}: ${e}`);
  }
  if (!r.ok) throw new ErroFonte(`${new URL(url).host}: http ${r.status}`);
  const t = await r.text();
  try {
    return JSON.parse(t);
  } catch {
    throw new ErroFonte(`${new URL(url).host} respondeu algo inesperado: ${t.slice(0, 200)}`);
  }
}

const qs = (o) => new URLSearchParams(Object.entries(o).filter(([, v]) => v !== undefined && v !== null)).toString();

export function semAcento(s) {
  return s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toUpperCase().trim();
}

// ---------------- São Paulo (GeoSampa) ----------------

const WFS = "https://wfs.geosampa.prefeitura.sp.gov.br/geoserver/ows";
const CAMADAS_ZONA = {
  2024: { camada: "perimetro_zona_lei_18177_24", norma_id: "LEI_18177_2024", descricao: "Mapa 1 da Lei 18.177/2024 (vigente)" },
  2016: { camada: "zoneamento_2016_map1", norma_id: "LEI_16402_2016", descricao: "Mapa 1 da Lei 16.402/2016 (substituído)" },
};
const CAMPOS_LOTE =
  "cd_setor_fiscal,cd_quadra_fiscal,cd_lote,cd_condominio,cd_digito_sql,tx_tipo_lote," +
  "nm_logradouro_completo,cd_numero_porta,qt_area_terreno,ge_poligono";
const CAMPOS_ZONA = "cd_zoneamento_perimetro,tx_zoneamento_perimetro";

const urlCamada = (c) => `${WFS}?service=WFS&version=2.0.0&request=GetFeature&typeNames=geoportal:${c}`;

async function wfs(get, camada, cql, campos) {
  const u = `${WFS}?${qs({
    service: "WFS", version: "2.0.0", request: "GetFeature", typeNames: "geoportal:" + camada,
    outputFormat: "application/json", count: 20, CQL_FILTER: cql, propertyName: campos,
  })}`;
  const d = await get(u);
  if (!d || !Array.isArray(d.features)) throw new ErroFonte(`GeoSampa (${camada}) respondeu algo inesperado`);
  return d.features;
}

export function lerSql(sql) {
  const m = /^\s*(\d{3})\D?(\d{3})\D?(\d{4})(?:\D?(\d))?\s*$/.exec(sql || "");
  if (!m) throw new ErroEntrada(`SQL inválido: ${sql}. Use setor.quadra.lote-dígito, ex.: 009.001.0074-1`);
  return m.slice(1);
}

const TIPOS = new Set(["RUA", "AVENIDA", "ALAMEDA", "TRAVESSA", "PRACA", "ESTRADA", "LARGO", "VIELA", "RODOVIA", "R", "AV"]);

export function normalizarLogradouro(txt) {
  const partes = semAcento(txt).replace(/[^A-Z0-9 ]/g, " ").split(/\s+/).filter(Boolean);
  if (partes.length && TIPOS.has(partes[0].replace(/\.$/, ""))) partes.shift();
  return partes.join(" ");
}

// WGS84/SIRGAS2000 -> UTM 23S (EPSG:31983). Erro < 1 m em SP.
export function latlonParaUtm23s(lat, lon) {
  const a = 6378137.0, f = 1 / 298.257222101, k0 = 0.9996, lon0 = (-45 * Math.PI) / 180;
  const e2 = f * (2 - f), ep2 = e2 / (1 - e2);
  const phi = (lat * Math.PI) / 180, lam = (lon * Math.PI) / 180;
  const n = a / Math.sqrt(1 - e2 * Math.sin(phi) ** 2);
  const t = Math.tan(phi) ** 2, c = ep2 * Math.cos(phi) ** 2, A = Math.cos(phi) * (lam - lon0);
  const m = a * ((1 - e2 / 4 - (3 * e2 ** 2) / 64 - (5 * e2 ** 3) / 256) * phi
    - ((3 * e2) / 8 + (3 * e2 ** 2) / 32 + (45 * e2 ** 3) / 1024) * Math.sin(2 * phi)
    + ((15 * e2 ** 2) / 256 + (45 * e2 ** 3) / 1024) * Math.sin(4 * phi)
    - ((35 * e2 ** 3) / 3072) * Math.sin(6 * phi));
  const x = k0 * n * (A + ((1 - t + c) * A ** 3) / 6 + ((5 - 18 * t + t * t + 72 * c - 58 * ep2) * A ** 5) / 120) + 500000;
  const y = k0 * (m + n * Math.tan(phi) * ((A * A) / 2 + ((5 - t + 9 * c + 4 * c * c) * A ** 4) / 24
    + ((61 - 58 * t + t * t + 600 * c - 330 * ep2) * A ** 6) / 720));
  return [x, y + 10000000];
}

function aneis(g) {
  if (g.type === "Polygon") return [g.coordinates];
  if (g.type === "MultiPolygon") return g.coordinates;
  throw new ErroFonte(`geometria não suportada: ${g.type}`);
}

function noAnel(x, y, anel) {
  let dentro = false;
  for (let i = 0, j = anel.length - 1; i < anel.length; j = i++) {
    const [xi, yi] = anel[i], [xj, yj] = anel[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) dentro = !dentro;
  }
  return dentro;
}

export function contem(g, x, y) {
  return aneis(g).some((p) => noAnel(x, y, p[0]) && !p.slice(1).some((b) => noAnel(x, y, b)));
}

export function amostras(g, n) {
  const pts = aneis(g).flatMap((p) => p[0]);
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const out = [];
  for (let i = 0; i < n; i++)
    for (let j = 0; j < n; j++) {
      const p = [x0 + ((x1 - x0) * (i + 0.5)) / n, y0 + ((y1 - y0) * (j + 0.5)) / n];
      if (contem(g, ...p)) out.push(p);
    }
  return out;
}

function wkt(g) {
  const anel = (a) => "(" + a.map((p) => `${p[0].toFixed(3)} ${p[1].toFixed(3)}`).join(", ") + ")";
  const polis = aneis(g);
  if (polis.length === 1) return "POLYGON(" + polis[0].map(anel).join(", ") + ")";
  return "MULTIPOLYGON(" + polis.map((p) => "(" + p.map(anel).join(", ") + ")").join(", ") + ")";
}

const cqlTxt = (s) => String(s).replace(/'/g, "''");
const ponto = (x, y) => `POINT(${x.toFixed(3)} ${y.toFixed(3)})`;
const zonaDe = (f, fracao = 1.0) => ({
  zona: f.properties.cd_zoneamento_perimetro, nome: f.properties.tx_zoneamento_perimetro, fracao,
});

async function zonasEm(get, alvo, versao, comFracao = true) {
  const camada = CAMADAS_ZONA[versao].camada;
  if (Array.isArray(alvo)) {
    const fs = await wfs(get, camada, `INTERSECTS(ge_poligono, ${ponto(...alvo)})`, CAMPOS_ZONA);
    return fs.slice(0, 1).map((f) => zonaDe(f));
  }
  const fs = await wfs(get, camada, `INTERSECTS(ge_poligono, ${wkt(alvo)})`, CAMPOS_ZONA);
  if (fs.length <= 1) return fs.map((f) => zonaDe(f));
  if (!comFracao) return fs.map((f) => zonaDe(f, null));
  // lote em mais de uma zona: fração estimada por amostragem (grade 5x5, no máximo 25 consultas em paralelo)
  let pts = amostras(alvo, 5);
  if (!pts.length) pts = amostras(alvo, 10).slice(0, 25);
  const res = await Promise.all(pts.map((p) => zonasEm(get, p, versao)));
  const conta = new Map();
  for (const z of res.flat()) {
    const k = `${z.zona}\u0000${z.nome}`;
    conta.set(k, (conta.get(k) || 0) + 1);
  }
  const tot = [...conta.values()].reduce((a, b) => a + b, 0) || 1;
  return [...conta.entries()]
    .map(([k, v]) => {
      const [zona, nome] = k.split("\u0000");
      return { zona, nome, fracao: Math.round((v / tot) * 100) / 100 };
    })
    .sort((a, b) => b.fracao - a.fracao);
}

// Macroárea do PDE (Mapa 2, Lei 18.209/2024). O GeoSampa não publica os subsetores da MEM (conferido em
// 17/11/2026): fora da MEM o lote não está no Arco Tietê; dentro, quem confirma é o usuário. Igual a lote.py.
const CAMADA_MACROAREA = "pde_macroarea_lei_18209";
const geoCql = (alvo) => (Array.isArray(alvo) ? ponto(...alvo) : wkt(alvo));

async function macroarea(get, alvo) {
  try {
    const fs = await wfs(get, CAMADA_MACROAREA, `INTERSECTS(ge_poligono, ${geoCql(alvo)})`, "sg_macroarea,nm_macroarea");
    return fs.map((f) => ({ sigla: (f.properties.sg_macroarea || "").trim(), nome: f.properties.nm_macroarea ?? null }));
  } catch (e) {
    if (e instanceof ErroFonte) return null;
    throw e;
  }
}

const SOBREPOSICOES = [
  ["envoltoria_condephaat", "patrimonio_cultural_area_envoltoria_CONDEPHAAT", "nm_area", "tx_resolucao_condephaat",
    "Área envoltória CONDEPHAAT: projeto depende de anuência prévia (gabarito, volumetria, materiais)."],
  ["envoltoria_conpresp", "patrimonio_cultural_area_envoltoria_CONPRESP", "nm_area", "tx_resolucao_conpresp",
    "Área envoltória CONPRESP: projeto depende de anuência prévia."],
  ["envoltoria_iphan", "patrimonio_cultural_area_envoltoria_IPHAN", "nm_area", "tx_resolucao_iphan",
    "Área envoltória IPHAN: projeto depende de anuência prévia."],
  ["operacao_urbana", "operacao_urbana", "nm_operacao_urbana", "tx_lei_operacao_urbana",
    "Operação urbana: o potencial adicional segue a lei da operação (ex.: CEPAC), não a outorga comum."],
  ["subsetor_operacao_urbana", "subsetor_operacao_urbana", null, null, null],
  ["aiu", "perimetro_aiu", "nm_lei", "cd_numero_lei", "Área de Intervenção Urbana: conferir os parâmetros e incentivos da lei da AIU."],
  ["requalifica_centro", "requalifica_centro_perimetro_geral", "nm_perimetro", "dc_lei",
    "Perímetro do Requalifica Centro (Lei 17.577/2021): regras próprias para reforma e mudança de uso."],
];

function textoDe(p, campo) {
  if (campo && p[campo]) return String(p[campo]).trim();
  for (const [k, v] of Object.entries(p)) if (typeof v === "string" && /^(nm_|tx_|sg_)/.test(k)) return v.trim();
  return null;
}

async function sobreposicoes(get, alvo, partes) {
  const geo = geoCql(alvo);
  const umaCamada = async ([tipo, camada, campoNome, campoNorma, aviso]) => {
    try {
      const fs = await wfs(get, camada, `INTERSECTS(ge_poligono, ${geo})`);
      return fs.map((f) => ({ tipo, camada, nome: textoDe(f.properties || {}, campoNome),
        norma: campoNorma ? f.properties?.[campoNorma] ?? null : null, aviso }));
    } catch (e) {
      if (e instanceof ErroFonte) return [{ tipo, camada, erro: e.message.slice(0, 120) }];
      throw e;
    }
  };
  const tombado = async () => {
    if (!partes) return [];
    const [setor, quadra, lt] = partes;
    try {
      const fs = await wfs(get, "patrimonio_cultural_bem_tombado",
        `cd_setor_fiscal='${setor}' AND cd_quadra_fiscal='${quadra}' AND cd_lote='${lt}'`);
      return fs.map((f) => {
        const p = f.properties || {};
        const norma = [p.tx_resolucao_conpresp, p.tx_resolucao_condephaat].filter((x) => x && x !== "não consta").join(" | ");
        return { tipo: "tombado", camada: "patrimonio_cultural_bem_tombado", nome: p.nm_area_tombada ?? null, norma: norma || null,
          aviso: "Bem tombado: qualquer intervenção depende de anuência prévia; demolição em regra vedada." };
      });
    } catch (e) {
      if (e instanceof ErroFonte) return [{ tipo: "tombado", camada: "patrimonio_cultural_bem_tombado", erro: e.message.slice(0, 120) }];
      throw e;
    }
  };
  const res = await Promise.all([...SOBREPOSICOES.map(umaCamada), tombado()]);
  return res.flat();
}

export function aplicarArcoTiete(r, forcar) {
  if (forcar != null) {
    r.arco_tiete = forcar;
    r.arco_tiete_origem = "informado pelo usuário";
  } else if (r.macroarea == null) {
    r.arco_tiete_origem = "não verificado (camada de macroáreas não respondeu)";
    if (r.zonas.includes("ZEM"))
      (r.avisos ??= []).push("Não deu para ver a macroárea do lote. Se ele estiver no Subsetor Arco Tietê da MEM, o CA máximo " +
        "da ZEM é 4,0 (Res. CTLU 004/2018): use arco_tiete=sim.");
  } else if (r.arco_tiete == null) {
    r.arco_tiete_origem = "lote na MEM; o subsetor não tem camada aberta no GeoSampa";
    if (r.zonas.includes("ZEM"))
      (r.avisos ??= []).push("Lote em ZEM na Macroárea de Estruturação Metropolitana (MEM). Se estiver no Subsetor Arco Tietê " +
        "(SISZON, camada MA), o CA máximo é 4,0 pela Res. CTLU 004/2018: confirme e use arco_tiete=sim. " +
        "Sem a confirmação, a busca usa o Quadro 3 (CA 2,0).");
  } else {
    r.arco_tiete_origem = "fora da MEM (camada pde_macroarea_lei_18209)";
  }
  if (r.arco_tiete) r.zonas = r.zonas.flatMap((z) => (z === "ZEM" ? ["ZEM", "ZEM / Arco Tietê"] : [z]));
}

export function avisosDeSobreposicao(r) {
  const vistos = new Set();
  for (const s of r.sobreposicoes || []) {
    const linha = s.erro
      ? `Camada ${s.camada} não respondeu: confira essa sobreposição no SISZON.`
      : `${s.nome || s.tipo}${s.norma ? ` (${s.norma})` : ""}${s.aviso ? `: ${s.aviso}` : ""}`;
    if (!vistos.has(linha)) {
      vistos.add(linha);
      (r.avisos ??= []).push(linha);
    }
  }
}

export async function saoPaulo({ sql, endereco, numero, lat, lon }, get = getJson) {
  let x, y, cql;
  if (lat != null) [x, y] = latlonParaUtm23s(lat, lon);
  if (sql) {
    const [setor, quadra, lote] = lerSql(sql);
    cql = `cd_setor_fiscal='${setor}' AND cd_quadra_fiscal='${quadra}' AND cd_lote='${lote}'`;
  } else if (endereco) {
    const nome = cqlTxt(normalizarLogradouro(endereco));
    cql = `(nm_logradouro_completo ILIKE '% ${nome}' OR nm_logradouro_completo ILIKE '${nome}')`;
    if (numero) cql += ` AND cd_numero_porta='${cqlTxt(String(numero).replace(/^0+/, ""))}'`;
  } else if (x != null) {
    cql = `INTERSECTS(ge_poligono, ${ponto(x, y)})`;
  } else {
    throw new ErroEntrada("informe sql, endereco (+numero) ou lat/lon");
  }
  const lotes = await wfs(get, "lote_cidadao", cql, CAMPOS_LOTE);
  lotes.sort((a, b) => (a.properties.tx_tipo_lote !== "FISCAL") - (b.properties.tx_tipo_lote !== "FISCAL"));
  const res = { cidade: "São Paulo", entrada: limpo({ sql, endereco, numero, lat, lon }) };
  let alvo;
  if (lotes.length) {
    const p = lotes[0].properties;
    res.lote = {
      sql: `${p.cd_setor_fiscal}.${p.cd_quadra_fiscal}.${p.cd_lote}` + (p.cd_digito_sql ? `-${p.cd_digito_sql}` : ""),
      condominio: p.cd_condominio ?? null, tipo: p.tx_tipo_lote ?? null,
      endereco: [p.nm_logradouro_completo, p.cd_numero_porta].filter(Boolean).join(" ") || null,
      area_terreno_m2: p.qt_area_terreno ?? null,
    };
    if (lotes.length > 1) res.outros_lotes = lotes.length - 1;
    alvo = lotes[0].geometry;
  } else if (x != null) {
    res.lote = null;
    res.aviso = "Ponto fora de lote cadastrado (rua ou área pública); zona do ponto.";
    alvo = [x, y];
  } else {
    throw new ErroFonte("Lote não encontrado no GeoSampa (lote_cidadao). Unidades de condomínio: use o endereço ou a coordenada do terreno.");
  }
  // 2016 só lista as zonas (sem fração) para caber no limite de subrequests
  const partes = lotes.length ? [lotes[0].properties.cd_setor_fiscal, lotes[0].properties.cd_quadra_fiscal, lotes[0].properties.cd_lote] : null;
  const [z24, z16, macro, sobre] = await Promise.all([
    zonasEm(get, alvo, 2024), zonasEm(get, alvo, 2016, false), macroarea(get, alvo), sobreposicoes(get, alvo, partes)]);
  res.macroarea = macro;
  res.arco_tiete = macro == null || macro.some((m) => m.sigla === "MEM") ? null : false;
  res.sobreposicoes = sobre;
  for (const [v, zs] of [[2024, z24], [2016, z16]]) {
    const cfg = CAMADAS_ZONA[v];
    res[`zonas_${v}`] = zs;
    res[`fonte_${v}`] = { camada: "geoportal:" + cfg.camada, norma_id: cfg.norma_id, descricao: cfg.descricao, url: urlCamada(cfg.camada) };
  }
  const a = z24.map((z) => z.zona).sort().join(), b = z16.map((z) => z.zona).sort().join();
  res.mudou_desde_2016 = a !== b;
  res.zona = z24[0]?.zona ?? null;
  res.zonas = z24.map((z) => z.zona);
  return res;
}

// ---------------- Manaus (ArcGIS da IMPLURB) ----------------

const IMPLURB = "https://pmm.manaus.am.gov.br/arcgis/rest/services/IMPLURB/IMPLURB_PATRIMONIO_HISTORICO_SRGS/MapServer";
const GEOCODE_MAO = "https://pmm.manaus.am.gov.br/arcgis/rest/services/GEOCODE/GeocodeManauaraDEF_07102025/GeocodeServer";
const CAMADAS_MAO = { lotes: 3, subsetores: 4, setores: 5, bairros: 6 };

async function arcgis(get, url, params, nome) {
  const d = await get(`${url}?${qs({ ...params, f: "json" })}`);
  if (d?.error) throw new ErroFonte(`ArcGIS ${nome}: ${JSON.stringify(d.error)}`);
  return d;
}

async function maoPorPonto(get, camada, lon, lat, campos, distancia) {
  const p = {
    geometry: `${lon},${lat}`, geometryType: "esriGeometryPoint", inSR: 4326,
    spatialRel: "esriSpatialRelIntersects", outFields: campos, returnGeometry: "false",
  };
  if (distancia) Object.assign(p, { distance: distancia, units: "esriSRUnit_Meter" });
  const d = await arcgis(get, `${IMPLURB}/${CAMADAS_MAO[camada]}/query`, p, "Manaus");
  return (d.features || []).map((f) => f.attributes);
}

const EXTENSO = { UM: 1, DOIS: 2, TRES: 3, QUATRO: 4, CINCO: 5, SEIS: 6, SETE: 7, OITO: 8, NOVE: 9, DEZ: 10, ONZE: 11,
  DOZE: 12, TREZE: 13, CATORZE: 14, QUATORZE: 14, QUINZE: 15, DEZESSEIS: 16, DEZESSETE: 17, DEZOITO: 18, DEZENOVE: 19,
  VINTE: 20, TRINTA: 30, PRIMEIRO: 1 };
const MESES = new Set(["JANEIRO", "FEVEREIRO", "MARCO", "ABRIL", "MAIO", "JUNHO", "JULHO", "AGOSTO", "SETEMBRO",
  "OUTUBRO", "NOVEMBRO", "DEZEMBRO"]);

// 'Avenida Sete de Setembro, 895' -> também 'AVENIDA 7 DE SETEMBRO, 895' (o geocodificador de Manaus usa algarismos)
export function variantesEndereco(endereco) {
  const p = semAcento(endereco).replace(/,/g, " , ").split(/\s+/).filter(Boolean);
  let mudou = false;
  for (let i = 0; i < p.length - 2; i++)
    if (EXTENSO[p[i]] && p[i + 1] === "DE" && MESES.has(p[i + 2])) { p[i] = String(EXTENSO[p[i]]); mudou = true; }
  return mudou ? [endereco, p.join(" ").replace(/ , /g, ", ")] : [endereco];
}

const titulo = (s) => s.toLowerCase().replace(/(^|\s)(\p{L})/gu, (m, a, b) => a + b.toUpperCase());

// iptu: o número de IPTU do imóvel, que a camada LOTES guarda no campo MATRICULA (AG 0230: IPTU 5341 -> CGL 0200420373).
async function loteManaus(get, where, rotulo) {
  const d = await arcgis(get, `${IMPLURB}/${CAMADAS_MAO.lotes}/query`, {
    where, outFields: "CGL,SETOR,QUADRA,LOTE,MATRICULA,SHAPE.AREA", returnGeometry: "true", outSR: 4326,
  }, "Manaus");
  const fs = d.features || [];
  if (!fs.length) throw new ErroFonte(`${rotulo} não encontrado na camada LOTES da IMPLURB`);
  const anel = fs[0].geometry.rings[0];
  return [fs[0].attributes, anel.reduce((s, p) => s + p[1], 0) / anel.length, anel.reduce((s, p) => s + p[0], 0) / anel.length];
}

export async function manaus({ lat, lon, cgl, endereco, iptu }, get = getJson) {
  const res = { cidade: "Manaus", entrada: limpo({ lat, lon, cgl, iptu, endereco }) };
  const avisos = [];
  const CAMPOS = "CGL,SETOR,QUADRA,LOTE,MATRICULA,SHAPE.AREA";
  let lotes;
  if (cgl) {
    let a;
    [a, lat, lon] = await loteManaus(get, `CGL='${String(cgl).replace(/'/g, "")}'`, `CGL ${cgl}`);
    lotes = [a];
  } else if (iptu) {
    const num = String(iptu).replace(/\D/g, "");
    if (!num) throw new ErroEntrada("iptu deve ser um número");
    let a;
    [a, lat, lon] = await loteManaus(get, `MATRICULA=${parseInt(num, 10)}`, `IPTU ${iptu}`);
    lotes = [a];
  } else {
    if (endereco) {
      let c = null;
      for (const tentativa of variantesEndereco(endereco)) {
        const d = await arcgis(get, `${GEOCODE_MAO}/findAddressCandidates`, { SingleLine: tentativa, outSR: 4326, maxLocations: 1 }, "Manaus");
        const x = (d.candidates || [])[0];
        if (x && (x.score || 0) >= 80) { c = x; break; }
      }
      if (!c) throw new ErroFonte(`Endereço não encontrado no geocodificador de Manaus: ${endereco} (dica: números em algarismos, como 'Av. 7 de Setembro, 895')`);
      [lat, lon] = [c.location.y, c.location.x];
      res.endereco_encontrado = c.address;
    }
    if (lat == null) throw new ErroEntrada("informe lat/lon, cgl, iptu ou endereco");
    lotes = await maoPorPonto(get, "lotes", lon, lat, CAMPOS);
    if (!lotes.length) {
      lotes = await maoPorPonto(get, "lotes", lon, lat, CAMPOS, 20);
      if (lotes.length)
        avisos.push(`O ponto caiu fora de lote (na rua); usei o lote mais próximo, CGL ${lotes[0].CGL}, de ${lotes.length} a menos de 20 m. ` +
          "Pode ser o vizinho: confira pela área ou busque por iptu ou cgl.");
    }
  }
  res.ponto = { lat, lon };
  if (lotes.length) {
    const l = lotes[0];
    res.lote = { cgl: l.CGL ?? null, setor_fiscal: l.SETOR ?? null, quadra: l.QUADRA ?? null, lote: l.LOTE ?? null,
      iptu: l.MATRICULA ?? null, area_m2: l["SHAPE.AREA"] ? Math.round(l["SHAPE.AREA"] * 100) / 100 : null };
  }
  const [setores, sub, bai, perto] = await Promise.all([
    maoPorPonto(get, "setores", lon, lat, "SETOR,BAIRRO"),
    maoPorPonto(get, "subsetores", lon, lat, "SETOR_"),
    maoPorPonto(get, "bairros", lon, lat, "NOME_BAIRR"),
    maoPorPonto(get, "subsetores", lon, lat, "SETOR_", 30),
  ]);
  const zonas = [];
  if (setores[0]?.SETOR) zonas.push(`Setor ${setores[0].SETOR}`);
  if (sub[0]?.SETOR_) zonas.push(titulo(sub[0].SETOR_.trim())); // mesmo nome que o leitor manaus_setores grava
  // subsetor a menos de 30 m: o lote pode estar na divisa (AG 0230: o laudo diz Sítio Histórico; a camada, não)
  const proximos = [...new Set(perto.filter((x) => x.SETOR_).map((x) => titulo(x.SETOR_.trim())))].filter((n) => !zonas.includes(n)).sort();
  for (const nome of proximos)
    avisos.push(`O lote está a menos de 30 m do ${nome}, mas fora dele pela camada da IMPLURB. ` +
      "Confira a divisa no Anexo da Lei 1.838/2014: se o lote estiver dentro, valem os parâmetros do subsetor.");
  res.subsetores_proximos = proximos;
  res.bairro = bai[0]?.NOME_BAIRR ?? null;
  res.zonas = zonas;
  res.fonte = { camadas: ["setores", "subsetores", "lotes"].map((c) => `${IMPLURB}/${CAMADAS_MAO[c]}`), norma_id: "LEI_1838_2014" };
  if (!zonas.length) avisos.push("Ponto fora dos setores da IMPLURB (zona turística ou fora do perímetro urbano?).");
  res.avisos = avisos;
  return res;
}

// ---------------- Natal (portal ArcGIS da SEMURB + Anexo II da LC 208/2022) ----------------

const GEO_NATAL = "https://geo.natal.rn.gov.br/server/rest/services/Hosted";
const SOBREPOSICOES_NATAL = {
  ZA_revetorizada: "Zona Adensável", AEITP: "Área Especial de Interesse Turístico e Paisagístico",
  AERU: "Área Especial de Requalificação Urbana", AEIS: "Área Especial de Interesse Social",
  AEPC: "Área Especial de Preservação Cultural", AECE: "AECE", AEM: "AEM",
  ZPA_1_merge: "ZPA-1", ZPA_2: "ZPA-2", ZPA_3: "ZPA-3", ZPA_4: "ZPA-4", ZPA_5: "ZPA-5",
  ZPA_6: "ZPA-6", ZPA_7: "ZPA-7", ZPA_8: "ZPA-8", ZPA_9: "ZPA-9", ZPA_10_Merge: "ZPA-10",
};

async function natalPonto(get, servico, lat, lon, campos) {
  const d = await arcgis(get, `${GEO_NATAL}/${servico}/FeatureServer/0/query`, {
    geometry: `${lon},${lat}`, geometryType: "esriGeometryPoint", inSR: 4326,
    spatialRel: "esriSpatialRelIntersects", outFields: campos, returnGeometry: "false",
  }, "Natal");
  return (d.features || []).map((f) => f.attributes);
}

// nomes = zonas de Natal cadastradas na base (SELECT DISTINCT zona ...)
export async function natal({ bairro, bacia, eixo, lat, lon }, nomes, get = getJson) {
  const res = { cidade: "Natal", entrada: limpo({ bairro, bacia, eixo, lat, lon }) };
  if (lat != null) {
    const servs = Object.keys(SOBREPOSICOES_NATAL);
    const [b, ...sobre] = await Promise.all([
      natalPonto(get, "Bairros", lat, lon, "bairro,reg_adm"),
      ...servs.map((s) => natalPonto(get, s, lat, lon, "objectid")),
    ]);
    res.bairro_oficial = b[0]?.bairro ?? null;
    res.regiao_administrativa = b[0]?.reg_adm ?? null;
    res.sobreposicoes = servs.filter((s, i) => sobre[i].length).map((s) => SOBREPOSICOES_NATAL[s]);
    res.fonte_ponto = `${GEO_NATAL}/Bairros e camadas de zonas/áreas especiais (geo.natal.rn.gov.br)`;
    bairro = bairro || res.bairro_oficial;
  }
  const zonas = [], avisos = [];
  const achar = (prefixo, nome) => {
    const alvo = semAcento(`${prefixo} ${nome}`);
    const resto = (z) => !z.slice(prefixo.length + 1).includes("/");
    const c = nomes.filter((z) => semAcento(z) === alvo);
    if (c.length) return c[0];
    const c2 = nomes.filter((z) => semAcento(z).startsWith(alvo) && resto(z));
    if (c2.length) return c2[0];
    return nomes.find((z) => semAcento(z).includes(alvo) && !z.includes("/")) ?? null;
  };
  let b = null;
  if (bairro) {
    b = achar("Bairro", bairro);
    if (b) zonas.push(b);
    else avisos.push(`Bairro '${bairro}' não está nos Quadros 1.1/1.2.`);
  }
  if (bacia) {
    bacia = bacia.toUpperCase().trim();
    if (nomes.includes(`Bacia ${bacia}`)) zonas.push(`Bacia ${bacia}`);
    else avisos.push(`Bacia '${bacia}' não existe (use AN..QN ou AS..OS).`);
    if (b && nomes.includes(`${b} / Bacia ${bacia}`)) zonas.push(`${b} / Bacia ${bacia}`);
    else if (b) avisos.push(`A bacia ${bacia} não passa pelo ${b} segundo os Quadros 1.1/1.2.`);
  } else if (b) {
    const opc = nomes.filter((z) => z.startsWith(b + " / Bacia ")).map((z) => z.split("/ Bacia ")[1]).sort();
    if (opc.length) {
      res.bacias_do_bairro = opc;
      avisos.push("Informe a bacia do lote (Mapas 2/2A) para o CA exato; o CA do bairro é o maior entre as bacias.");
    }
  }
  if (eixo) {
    const e = [...new Set(nomes.filter((z) => z.startsWith("Eixo ") && semAcento(z).includes(semAcento(eixo))
      && (!bacia || z.endsWith(`/ Bacia ${bacia}`))))].sort();
    if (e.length) zonas.push(...e);
    else avisos.push(`Eixo '${eixo}' não encontrado nos Quadros 1.3/1.4` + (bacia ? ` para a bacia ${bacia}.` : "."));
  }
  if (res.sobreposicoes) {
    const esp = res.sobreposicoes.filter((x) => x !== "Zona Adensável");
    if (esp.length)
      avisos.push(`Lote em ${esp.join(", ")}: valem as prescrições próprias dessas áreas (regulamentação específica), que podem restringir os parâmetros abaixo.`);
    if (!res.sobreposicoes.includes("Zona Adensável"))
      avisos.push("Ponto fora da Zona Adensável: acima do CA básico 1,0 não há outorga onerosa (art. 13).");
  }
  if (!zonas.length) avisos.push("Sem bairro/bacia, só valem os parâmetros gerais do município.");
  zonas.push("Município (geral)");
  res.zonas = zonas;
  res.avisos = avisos;
  res.fonte = { norma_id: "LCP_208_2022", descricao: "Anexo II (Quadros 1.1 a 1.4); bacia do lote nos Mapas 2/2A" };
  return res;
}

// ---------------- precedência: qual zona vale para cada parâmetro ----------------

// Ordem do geral para o específico: o nível maior prevalece. Igual a _nivel em busca.py.
export function nivel(slug, zona) {
  if (slug === "sao-paulo") return zona.includes(" / ") ? 1 : 0;
  if (slug === "manaus") return zona.startsWith("Subsetor") ? 1 : 0;
  if (slug === "natal") {
    if (zona === "Município (geral)") return 0;
    if (zona.startsWith("Eixo ")) return 4;
    if (zona.startsWith("Bacia ")) return 2;
    if (zona.startsWith("Bairro ")) return zona.includes(" / Bacia ") ? 3 : 1;
  }
  return 0;
}

// {parametro: valor aplicável ao lote}; null quando o lote de SP cai em mais de uma zona base.
export function aplicaveis(slug, zonas, parametros) {
  if (!zonas.length) return {};
  if (slug === "sao-paulo" && zonas.filter((z) => nivel(slug, z) === 0).length > 1) return null;
  const out = {};
  const ordem = zonas.map((z, i) => [z, i]).sort((a, b) => nivel(slug, a[0]) - nivel(slug, b[0]) || a[1] - b[1]).map((x) => x[0]);
  for (const z of ordem)
    for (const p of parametros[z] || []) {
      const item = { valor: p.valor, zona: z, norma_id: p.norma_id, dispositivo: p.dispositivo, nota: p.nota, conferido: p.conferido };
      const ant = out[p.parametro];
      if (ant) item.substitui = [...(ant.substitui || []), { zona: ant.zona, valor: ant.valor }];
      out[p.parametro] = item;
    }
  if (slug === "manaus") {
    const topo = Math.max(...zonas.map((z) => nivel(slug, z)));
    for (const v of Object.values(out)) if (nivel(slug, v.zona) < topo) v.herdado = true;
  }
  return out;
}

// ---------------- D1 ----------------

// Parâmetros vigentes hoje, ou numa data (AAAA-MM-DD) — útil para comparar com laudos antigos.
export function filtroVigencia(data) {
  if (!data) return { sql: "vigente_ate IS NULL", args: [] };
  if (!/^\d{4}-\d{2}-\d{2}$/.test(data)) throw new ErroEntrada("data deve ser AAAA-MM-DD");
  return { sql: "vigente_desde <= ? AND (vigente_ate IS NULL OR vigente_ate > ?)", args: [data, data] };
}

export async function parametrosDe(db, cidade, zonas, data) {
  if (!zonas.length) return {};
  const v = filtroVigencia(data);
  const r = await db.prepare(
    `SELECT zona, parametro, valor, nota, norma_id, dispositivo, fonte, vigente_desde, vigente_ate, origem, conferido
     FROM parametros WHERE cidade=? AND ${v.sql} AND zona IN (${zonas.map(() => "?").join(",")})
     ORDER BY zona, parametro`
  ).bind(cidade, ...v.args, ...zonas).all();
  const out = Object.fromEntries(zonas.map((z) => [z, []]));
  for (const p of r.results) out[p.zona].push(p);
  return out;
}

export async function regrasDe(db, cidade, zonas) {
  const r = await db.prepare(`SELECT * FROM regras WHERE cidade=? ORDER BY norma_id, dispositivo`).bind(cidade).all();
  return r.results.filter((g) => g.zonas.split(",").some((z) => z === "todas" || zonas.includes(z)));
}

const CIDADES = { "sao-paulo": "São Paulo", manaus: "Manaus", natal: "Natal" };

export function slugCidade(c) {
  if (!c) return null;
  if (CIDADES[c]) return c;
  const alvo = semAcento(c);
  return Object.keys(CIDADES).find((k) => semAcento(CIDADES[k]) === alvo) ?? null;
}

// p: URLSearchParams ou objeto simples
export async function buscar(db, p, get = getJson) {
  const g = (k) => (p.get ? p.get(k) : p[k]) ?? undefined;
  const num = (k) => {
    const v = g(k);
    if (v === undefined || v === "") return undefined;
    const n = Number(String(v).replace(",", "."));
    if (!Number.isFinite(n)) throw new ErroEntrada(`${k} inválido: ${v}`);
    return n;
  };
  const slug = slugCidade(g("cidade"));
  if (!slug) throw new ErroEntrada(`cidade sem busca: ${g("cidade") ?? "(vazia)"}. Use sao-paulo, manaus ou natal.`);
  const lat = num("lat"), lon = num("lon");
  if ((lat === undefined) !== (lon === undefined)) throw new ErroEntrada("informe lat e lon juntos");
  const data = g("data");
  filtroVigencia(data); // valida antes de gastar subrequests

  let r;
  if (slug === "sao-paulo") {
    const a = String(g("arco_tiete") ?? "").toLowerCase();
    if (a && !["sim", "nao", "não"].includes(a)) throw new ErroEntrada("arco_tiete deve ser sim ou nao");
    r = await saoPaulo({ sql: g("sql"), endereco: g("endereco"), numero: g("numero"), lat, lon }, get);
    aplicarArcoTiete(r, a ? a === "sim" : null);
    avisosDeSobreposicao(r);
  } else if (slug === "manaus") {
    r = await manaus({ lat, lon, cgl: g("cgl"), endereco: g("endereco"), iptu: g("iptu") }, get);
  } else {
    const nomes = (await db.prepare(`SELECT DISTINCT zona FROM parametros WHERE cidade='Natal'`).all()).results.map((x) => x.zona);
    r = await natal({ bairro: g("bairro"), bacia: g("bacia"), eixo: g("eixo"), lat, lon }, nomes, get);
  }
  const cidade = CIDADES[slug];
  r.parametros = await parametrosDe(db, cidade, r.zonas, data);
  r.regras = await regrasDe(db, cidade, r.zonas);
  r.aplicaveis = aplicaveis(slug, r.zonas, r.parametros);
  if (r.aplicaveis === null)
    (r.avisos ??= []).push("Lote em mais de uma zona: veja os parâmetros de cada zona (a parte de cada uma está em zonas_2024).");
  if (data) {
    r.data_referencia = data;
    r.aviso_data = "Parâmetros na versão vigente nessa data. As regras textuais não têm data de vigência na base: confira se já valiam.";
  }
  const todos = Object.values(r.parametros).flat();
  const nao = todos.filter((x) => !x.conferido).length;
  if (nao) r.aviso_conferencia = `${nao} de ${todos.length} valores ainda não conferidos (conferido=0): servem para teste, não para laudo.`;
  return r;
}

function limpo(o) {
  return Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== ""));
}
