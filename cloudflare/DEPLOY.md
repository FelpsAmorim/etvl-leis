# Subir o evtl-leis na Cloudflare (plano free)

## Como fica

| Peça | Onde roda | Por quê |
| --- | --- | --- |
| Consulta (`/consultar`, `/historico`, `/normas`, `/avisos`) | Worker + D1 | leve, cabe nos 10 ms de CPU do plano free |
| Busca do lote (`/buscar`) | Worker + D1 + GeoSampa/ArcGIS | espera de rede não conta como CPU; no máximo 37 subrequests (limite free: 50) |
| Vigia semanal (Querido Diário) | Cron do Worker, segunda 06:00 | só chamadas HTTP |
| Diagnóstico de rede (`/diagnostico`) | Worker | mostra se a rede da Cloudflare alcança cada fonte |
| Extração dos PDFs + consolidação | GitHub Actions (grátis) ou seu Mac | ler PDF passa de 10 ms de CPU |

Limites do free que importam: 100 mil requisições/dia, 10 ms de CPU por requisição, 50 chamadas externas por requisição, D1 com 5 GB e 5 milhões de leituras/dia. Folgado para o piloto.

## Passo a passo (uns 15 minutos, no seu Mac)

1. Crie a conta (sugiro uma conta nova no nome da startup, separada das contas da OKN) em https://dash.cloudflare.com/sign-up.
2. Instale e entre:

       cd evtl-leis/cloudflare
       npm install
       npx wrangler login

3. Crie o banco e cole o `database_id` que aparecer no `wrangler.toml`:

       npx wrangler d1 create evtl-leis

4. Crie as tabelas e carregue os dados:

       npm run d1:esquema
       cd .. && python3 -m evtl_leis atualizar --todas && python3 -m evtl_leis exportar && cd cloudflare
       npm run d1:dados

5. (Opcional) Proteja a API com um token:

       npx wrangler secret put API_TOKEN

6. Publique:

       npm run deploy

   Sai um endereço tipo `https://evtl-leis.<sua-conta>.workers.dev`.

7. **Teste de rede:** abra `https://evtl-leis.<sua-conta>.workers.dev/diagnostico`. Se as fontes das prefeituras aparecerem "ok", a questão do host está resolvida para a consulta e a vigia.

## Extração automática pelo GitHub Actions

O arquivo `.github/workflows/atualizar.yml` roda toda segunda: baixa os quadros, consolida, exporta e carrega no D1. No repositório, em Settings > Secrets and variables > Actions, crie:

- `CLOUDFLARE_API_TOKEN`: token com permissão "D1 Edit" (dash.cloudflare.com > My Profile > API Tokens)
- `CLOUDFLARE_ACCOUNT_ID`: aparece na lateral do painel da Cloudflare
- `ANTHROPIC_API_KEY`: opcional, só se usar o modo `--modelo`

Os servidores do GitHub ficam nos EUA. Se algum site de prefeitura bloquear IP de fora do Brasil, o passo "Diagnóstico de rede" do job mostra isso. Nesse caso a extração roda no seu Mac (passo 4) e só a carga vai pela nuvem.

## Testado aqui

`wrangler dev` local com D1 local: `/`, `/consultar` (SP/ZEM: 10 parâmetros + 7 regras), `/historico` (Natal) e o cron responderam certo. `/diagnostico` e a vigia rodaram, mas as chamadas externas foram bloqueadas pela rede do ambiente de desenvolvimento, não pelo código.

Testes do Worker (offline, D1 trocado por SQLite em memória com `d1_dados.sql`): `cd cloudflare && npm test`. Exige Node 22.5+.
