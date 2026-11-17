// evtl-leis na Cloudflare (Workers + D1, plano free).
// A extração pesada (PDF) roda fora (Mac ou GitHub Actions) e chega aqui pelo d1_dados.sql.
// Aqui ficam: consulta, histórico, avisos, diagnóstico de rede e a vigia semanal (cron).

import FONTES from "./fontes.json" with { type: "json" };
import { buscar, filtroVigencia, ErroEntrada, ErroFonte } from "./busca.js";

const QD = "https://api.queridodiario.ok.org.br/gazettes";
const UA = { "User-Agent": "evtl-leis/0.2 (+consulta de legislacao urbanistica)" };

const json = (dados, status = 200) =>
  new Response(JSON.stringify(dados, null, 2), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "access-control-allow-origin": "*" },
  });

function nomeCidade(c) {
  if (!c) return null;
  return FONTES.cidades[c]?.cidade ?? c; // aceita slug (sao-paulo) ou nome (São Paulo)
}

function autorizado(req, env) {
  if (!env.API_TOKEN) return true; // sem token configurado: leitura aberta
  const h = req.headers.get("authorization") || "";
  const q = new URL(req.url).searchParams.get("token");
  return h === `Bearer ${env.API_TOKEN}` || q === env.API_TOKEN;
}

async function testar(url) {
  const t0 = Date.now();
  try {
    const r = await fetch(url, { headers: UA, redirect: "follow", signal: AbortSignal.timeout(15000) });
    const ms = Date.now() - t0;
    // API que pede credencial/método (401/405) conta como alcançável
    const ok = r.ok || [401, 405].includes(r.status);
    return { estado: ok ? "ok" : "erro http", status: r.status, ms };
  } catch (e) {
    return { estado: e.name === "TimeoutError" ? "tempo esgotado" : "falha", erro: String(e).slice(0, 160), ms: Date.now() - t0 };
  }
}

async function diagnostico() {
  // só endereços da web (uma fonte citada por texto, como o DOM de Natal, não tem o que testar)
  const alvos = Object.entries(FONTES.alvos).filter(([, url]) => /^https?:\/\//.test(url));
  const res = await Promise.all(alvos.map(async ([nome, url]) => ({ nome, host: new URL(url).host, url, ...(await testar(url)) })));
  const ok = res.filter((r) => r.estado === "ok").length;
  return { alcancaveis: `${ok} de ${res.length}`, colo: null, resultados: res };
}

async function vigiar(env, desde) {
  let novos = 0;
  const erros = [];
  for (const [slug, c] of Object.entries(FONTES.cidades)) {
    for (const n of c.normas_base) {
      const q = new URLSearchParams({
        territory_ids: c.ibge, querystring: `"${n.termo}"`, published_since: desde,
        size: "20", excerpt_size: "400", number_of_excerpts: "2", sort_by: "descending_date",
      });
      try {
        const r = await fetch(`${QD}?${q}`, { headers: UA, signal: AbortSignal.timeout(20000) });
        if (!r.ok) { erros.push(`${slug}/${n.id}: http ${r.status}`); continue; }
        const d = await r.json();
        for (const g of d.gazettes || []) {
          const trecho = (g.excerpts || []).join(" … ").slice(0, 800);
          const res = await env.DB.prepare(
            `INSERT OR IGNORE INTO avisos (cidade, norma_id, data, fonte, url, trecho, criado_em)
             VALUES (?,?,?,?,?,?,?)`
          ).bind(c.cidade, n.id, g.date, "querido_diario", g.url || g.txt_url, trecho, new Date().toISOString()).run();
          novos += res.meta.changes || 0;
        }
      } catch (e) {
        erros.push(`${slug}/${n.id}: ${e}`);
      }
    }
  }
  return { novos, erros };
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const p = url.searchParams;
    if (!autorizado(req, env)) return json({ erro: "não autorizado" }, 401);

    switch (url.pathname) {
      case "/":
        return json({
          servico: "evtl-leis",
          rotas: {
            "/buscar?cidade=sao-paulo&sql=009.096.0000": "lote -> zona -> parâmetros + regras (ou &endereco=&numero=, &lat=&lon=)",
            "/buscar?cidade=manaus&endereco=Av Djalma Batista, 1661": "idem em Manaus (ou &cgl=, &lat=&lon=)",
            "/buscar?cidade=natal&bairro=Tirol&bacia=CS&eixo=Prudente de Morais": "idem em Natal (ou &lat=&lon= para bairro e áreas especiais)",
            "/consultar?cidade=sao-paulo&zona=ZEM": "parâmetros vigentes + regras",
            "&data=2022-05-01": "em /buscar e /consultar: parâmetros vigentes nessa data (comparar com laudos antigos)",
            "&arco_tiete=sim|nao": "em /buscar (SP): força o lote dentro/fora do Subsetor Arco Tietê; por padrão vem do GeoSampa",
            "/historico?cidade=sao-paulo&zona=ZEM&parametro=ca_max": "todas as versões",
            "/normas?cidade=sao-paulo": "leis cadastradas",
            "/avisos": "menções novas nos diários oficiais",
            "/diagnostico": "quais fontes a rede da Cloudflare alcança",
            "/vigiar?desde=2024-01-01": "roda a vigia agora (o cron faz isso toda segunda)",
          },
          cidades: Object.keys(FONTES.cidades),
        });

      case "/consultar": {
        const cidade = nomeCidade(p.get("cidade"));
        if (!cidade) return json({ erro: "informe ?cidade=" }, 400);
        const zona = p.get("zona");
        const data = p.get("data");
        let v;
        try {
          v = filtroVigencia(data);
        } catch (e) {
          return json({ erro: e.message }, 400);
        }
        const params = await env.DB.prepare(
          `SELECT zona, parametro, valor, nota, norma_id, dispositivo, fonte, vigente_desde, vigente_ate, origem, conferido
           FROM parametros WHERE cidade=? AND ${v.sql} ${zona ? "AND zona=?" : ""}
           ORDER BY zona, parametro`
        ).bind(cidade, ...v.args, ...(zona ? [zona] : [])).all();
        const regras = (await env.DB.prepare(`SELECT * FROM regras WHERE cidade=?`).bind(cidade).all()).results
          .filter((r) => !zona || r.zonas.split(",").some((z) => z === "todas" || z === zona));
        return json({ cidade, zona, data_referencia: data || null, parametros: params.results, regras });
      }

      case "/buscar": {
        try {
          return json(await buscar(env.DB, p));
        } catch (e) {
          if (e instanceof ErroEntrada) return json({ erro: e.message }, 400);
          if (e instanceof ErroFonte) return json({ erro: e.message, tipo: "fonte externa" }, 502);
          throw e;
        }
      }

      case "/historico": {
        const cidade = nomeCidade(p.get("cidade"));
        const r = await env.DB.prepare(
          `SELECT * FROM parametros WHERE cidade=? AND zona=? AND parametro=? ORDER BY vigente_desde`
        ).bind(cidade, p.get("zona"), p.get("parametro")).all();
        return json(r.results);
      }

      case "/normas": {
        const cidade = nomeCidade(p.get("cidade"));
        const r = cidade
          ? await env.DB.prepare(`SELECT * FROM normas WHERE cidade=? ORDER BY data`).bind(cidade).all()
          : await env.DB.prepare(`SELECT * FROM normas ORDER BY cidade, data`).all();
        return json(r.results);
      }

      case "/avisos": {
        const r = await env.DB.prepare(`SELECT * FROM avisos WHERE visto=0 ORDER BY data DESC LIMIT 100`).all();
        return json(r.results);
      }

      case "/diagnostico":
        return json({ ...(await diagnostico()), colo: req.cf?.colo, pais_saida: req.cf?.country });

      case "/vigiar": {
        const desde = p.get("desde") || new Date(Date.now() - 8 * 864e5).toISOString().slice(0, 10);
        return json(await vigiar(env, desde));
      }

      default:
        return json({ erro: "rota não encontrada" }, 404);
    }
  },

  async scheduled(evento, env, ctx) {
    const desde = new Date(Date.now() - 8 * 864e5).toISOString().slice(0, 10);
    ctx.waitUntil(vigiar(env, desde).then((r) => console.log("vigia", JSON.stringify(r))));
  },
};
