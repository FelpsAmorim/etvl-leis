// Busca do lote no Worker: endereço/coordenada/inscrição -> zona(s) -> parâmetros e regras do D1.
// Mesma lógica de evtl_leis/busca.py e evtl_leis/lote.py (a referência é o Python; mantenha os dois iguais).
//
// Limite do plano free: 50 subrequests por chamada. Pior caso aqui: SP com lote em várias zonas
// (1 lote + 1 zona + até 25 amostras + 1 de 2016 = 28); Natal com coordenada = 18; Manaus = 6.

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
  const [z24, z16] = await Promise.all([zonasEm(get, alvo, 2024), zonasEm(get, alvo, 2016, false)]);
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

const titulo = (s) => s.toLowerCase().replace(/(^|\s)(\p{L})/gu, (m, a, b) => a + b.toUpperCase());

export async function manaus({ lat, lon, cgl, endereco }, get = getJson) {
  const res = { cidade: "Manaus", entrada: limpo({ lat, lon, cgl, endereco }) };
  if (endereco) {
    const d = await arcgis(get, `${GEOCODE_MAO}/findAddressCandidates`, { SingleLine: endereco, outSR: 4326, maxLocations: 1 }, "Manaus");
    const c = (d.candidates || [])[0];
    if (!c || (c.score || 0) < 80) throw new ErroFonte(`Endereço não encontrado no geocodificador de Manaus: ${endereco}`);
    [lat, lon] = [c.location.y, c.location.x];
    res.endereco_encontrado = c.address;
  }
  const CAMPOS = "CGL,SETOR,QUADRA,LOTE";
  let lotes;
  if (cgl) {
    const d = await arcgis(get, `${IMPLURB}/${CAMADAS_MAO.lotes}/query`, {
      where: `CGL='${String(cgl).replace(/'/g, "")}'`, outFields: CAMPOS, returnGeometry: "true", outSR: 4326, returnCentroid: "true",
    }, "Manaus");
    const fs = d.features || [];
    if (!fs.length) throw new ErroFonte(`CGL ${cgl} não encontrado na camada LOTES da IMPLURB`);
    const anel = fs[0].geometry.rings[0];
    lon = anel.reduce((s, p) => s + p[0], 0) / anel.length;
    lat = anel.reduce((s, p) => s + p[1], 0) / anel.length;
    lotes = [fs[0].attributes];
  } else if (lat != null) {
    lotes = await maoPorPonto(get, "lotes", lon, lat, CAMPOS);
    if (!lotes.length) lotes = await maoPorPonto(get, "lotes", lon, lat, CAMPOS, 20);
  } else {
    throw new ErroEntrada("informe lat/lon, cgl ou endereco");
  }
  res.ponto = { lat, lon };
  if (lotes.length) {
    const l = lotes[0];
    res.lote = { cgl: l.CGL ?? null, setor_fiscal: l.SETOR ?? null, quadra: l.QUADRA ?? null, lote: l.LOTE ?? null };
    if (lotes.length > 1) res.aviso = `${lotes.length} lotes perto do ponto (o endereço caiu na rua); usei o primeiro. Confira o CGL.`;
  }
  const [setores, sub, bai] = await Promise.all([
    maoPorPonto(get, "setores", lon, lat, "SETOR,BAIRRO"),
    maoPorPonto(get, "subsetores", lon, lat, "SETOR_"),
    maoPorPonto(get, "bairros", lon, lat, "NOME_BAIRR"),
  ]);
  const zonas = [];
  if (setores[0]?.SETOR) zonas.push(`Setor ${setores[0].SETOR}`);
  if (sub[0]?.SETOR_) zonas.push(titulo(sub[0].SETOR_.trim())); // mesmo nome que o leitor manaus_setores grava
  res.bairro = bai[0]?.NOME_BAIRR ?? null;
  res.zonas = zonas;
  res.fonte = { camadas: ["setores", "subsetores", "lotes"].map((c) => `${IMPLURB}/${CAMADAS_MAO[c]}`), norma_id: "LEI_1838_2014" };
  if (!zonas.length) res.aviso = "Ponto fora dos setores da IMPLURB (zona turística ou fora do perímetro urbano?).";
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
    r = await saoPaulo({ sql: g("sql"), endereco: g("endereco"), numero: g("numero"), lat, lon }, get);
  } else if (slug === "manaus") {
    r = await manaus({ lat, lon, cgl: g("cgl"), endereco: g("endereco") }, get);
  } else {
    const nomes = (await db.prepare(`SELECT DISTINCT zona FROM parametros WHERE cidade='Natal'`).all()).results.map((x) => x.zona);
    r = await natal({ bairro: g("bairro"), bacia: g("bacia"), eixo: g("eixo"), lat, lon }, nomes, get);
  }
  const cidade = CIDADES[slug];
  r.parametros = await parametrosDe(db, cidade, r.zonas, data);
  r.regras = await regrasDe(db, cidade, r.zonas);
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
