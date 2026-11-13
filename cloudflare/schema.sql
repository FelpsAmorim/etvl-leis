-- Mesmo esquema da base local (evtl_leis/base.py)
CREATE TABLE IF NOT EXISTS normas (
  id TEXT PRIMARY KEY, cidade TEXT, tipo TEXT, numero TEXT, ano INTEGER,
  data TEXT, ementa TEXT, link TEXT, papel TEXT, vigencia TEXT, atualizado_em TEXT);
CREATE TABLE IF NOT EXISTS parametros (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cidade TEXT NOT NULL, zona TEXT NOT NULL, parametro TEXT NOT NULL,
  valor REAL, nota TEXT,
  norma_id TEXT, dispositivo TEXT, fonte TEXT,
  vigente_desde TEXT, vigente_ate TEXT,
  origem TEXT CHECK (origem IN ('extrator','alteracao','manual','modelo')),
  conferido INTEGER DEFAULT 0, criado_em TEXT);
CREATE INDEX IF NOT EXISTS ix_param ON parametros(cidade, zona, parametro, vigente_ate);
CREATE TABLE IF NOT EXISTS regras (
  id INTEGER PRIMARY KEY AUTOINCREMENT, cidade TEXT, zonas TEXT, texto TEXT,
  norma_id TEXT, dispositivo TEXT, UNIQUE(cidade, norma_id, dispositivo));
CREATE TABLE IF NOT EXISTS avisos (
  id INTEGER PRIMARY KEY AUTOINCREMENT, cidade TEXT, norma_id TEXT, data TEXT,
  fonte TEXT, url TEXT, trecho TEXT, visto INTEGER DEFAULT 0, criado_em TEXT,
  UNIQUE(cidade, norma_id, url));
