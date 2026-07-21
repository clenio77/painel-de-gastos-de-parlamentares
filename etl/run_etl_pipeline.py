"""
CVP-IA ETL pipeline — coleta Câmara/Senado, scoring, enriquecimento e migração Supabase.

Uso:
  python -m etl.run_etl_pipeline
  # ou
  python etl/run_etl_pipeline.py

Variáveis (.env):
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
  ETL_MONTHS=6
  SQLITE_PATH=candidatos.db
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cvp-etl")

CAMARA = "https://dadosabertos.camara.leg.br/api/v2"
SENADO = "https://legis.senado.leg.br/dadosabertos"


def http_get_json(url: str, headers: dict | None = None, retries: int = 3) -> Any:
    hdrs = {"Accept": "application/json", "User-Agent": "CVP-IA-ETL/1.0"}
    if headers:
        hdrs.update(headers)
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = Request(url, headers=hdrs)
            with urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET failed {url}: {last_err}")


def months_back(n: int) -> list[tuple[int, int]]:
    """Return list of (year, month) including current, n months total."""
    today = date.today()
    out = []
    y, m = today.year, today.month
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(out))


def period_label(y: int, m: int) -> str:
    return f"{m:02d}/{y}"


# ---------- SQLite ----------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS parlamentares (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  id_api TEXT,
  nome_parlamentar TEXT NOT NULL,
  partido TEXT,
  uf TEXT,
  casa TEXT,
  UNIQUE(casa, id_api)
);
CREATE TABLE IF NOT EXISTS scores (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  parlamentar_id INTEGER NOT NULL,
  periodo TEXT NOT NULL,
  score_geral REAL,
  score_atividade REAL,
  score_gasto REAL,
  score_transparencia REAL,
  score_coerencia REAL,
  dados_insuficientes INTEGER DEFAULT 0,
  detalhes TEXT,
  UNIQUE(parlamentar_id, periodo)
);
CREATE TABLE IF NOT EXISTS despesas (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  parlamentar_id INTEGER NOT NULL,
  periodo TEXT,
  tipo_despesa TEXT,
  fornecedor TEXT,
  cnpj_fornecedor TEXT,
  data_documento TEXT,
  valor REAL,
  url_documento TEXT,
  chave_natural TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS votacoes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  id_api TEXT,
  casa TEXT,
  descricao TEXT,
  data TEXT,
  UNIQUE(casa, id_api)
);
CREATE TABLE IF NOT EXISTS votos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  votacao_id INTEGER NOT NULL,
  parlamentar_id INTEGER NOT NULL,
  voto TEXT,
  descricao_ausencia TEXT,
  divergiu_orientacao INTEGER DEFAULT 0,
  UNIQUE(votacao_id, parlamentar_id)
);
CREATE TABLE IF NOT EXISTS proposicoes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  parlamentar_id INTEGER,
  casa TEXT,
  id_api TEXT,
  sigla_tipo TEXT,
  numero TEXT,
  ano INTEGER,
  ementa TEXT,
  situacao TEXT,
  url_oficial TEXT,
  UNIQUE(casa, id_api)
);
CREATE TABLE IF NOT EXISTS candidaturas_tse (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  parlamentar_id INTEGER,
  nome_parlamentar TEXT,
  cargo TEXT,
  ano_eleicao INTEGER,
  total_bens REAL,
  total_receitas REAL,
  UNIQUE(parlamentar_id, ano_eleicao)
);
CREATE TABLE IF NOT EXISTS fornecedores (
  cnpj TEXT PRIMARY KEY,
  razao_social TEXT,
  situacao TEXT,
  sancionado_tcu INTEGER DEFAULT 0,
  detalhe TEXT,
  verificado_em TEXT
);
CREATE TABLE IF NOT EXISTS pipeline_meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
"""


def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    log.info("Database initialized at %s", path)
    return conn


def upsert_parlamentar(conn: sqlite3.Connection, casa: str, id_api: str, nome: str, partido: str, uf: str) -> int:
    conn.execute(
        """
        INSERT INTO parlamentares (id_api, nome_parlamentar, partido, uf, casa)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(casa, id_api) DO UPDATE SET
          nome_parlamentar=excluded.nome_parlamentar,
          partido=excluded.partido,
          uf=excluded.uf
        """,
        (str(id_api), nome, partido, uf, casa),
    )
    row = conn.execute(
        "SELECT id FROM parlamentares WHERE casa=? AND id_api=?",
        (casa, str(id_api)),
    ).fetchone()
    return int(row["id"])


# ---------- Collectors ----------

def fetch_deputados() -> list[dict]:
    data = http_get_json(f"{CAMARA}/deputados?ordem=ASC&ordenarPor=nome")
    return data.get("dados", [])


def fetch_senadores() -> list[dict]:
    # Senado open data lista
    try:
        data = http_get_json(f"{SENADO}/senador/lista/atual.json")
        lista = (
            data.get("ListaParlamentarEmExercicio", {})
            .get("Parlamentares", {})
            .get("Parlamentar", [])
        )
        if isinstance(lista, dict):
            lista = [lista]
        out = []
        for item in lista or []:
            ident = item.get("IdentificacaoParlamentar", {})
            out.append(
                {
                    "id": ident.get("CodigoParlamentar"),
                    "nome": ident.get("NomeParlamentar") or ident.get("NomeCompletoParlamentar"),
                    "partido": ident.get("SiglaPartidoParlamentar"),
                    "uf": ident.get("UfParlamentar"),
                }
            )
        return [x for x in out if x.get("id")]
    except Exception as e:  # noqa: BLE001
        log.warning("Senado lista falhou: %s", e)
        return []


def fetch_despesas_deputado(id_api: str, year: int, month: int) -> list[dict]:
    q = urlencode({"ano": year, "mes": month, "itens": 100})
    url = f"{CAMARA}/deputados/{id_api}/despesas?{q}"
    try:
        data = http_get_json(url)
        return data.get("dados", [])
    except Exception as e:  # noqa: BLE001
        log.debug("despesas dep %s: %s", id_api, e)
        return []


def fetch_proposicoes_autor(id_api: str, year: int) -> list[dict]:
    q = urlencode({"idDeputadoAutor": id_api, "ano": year, "itens": 50, "ordem": "DESC", "ordenarPor": "id"})
    try:
        data = http_get_json(f"{CAMARA}/proposicoes?{q}")
        return data.get("dados", [])
    except Exception as e:  # noqa: BLE001
        log.debug("proposicoes %s: %s", id_api, e)
        return []


def fetch_votacoes_camara(year: int, month: int) -> list[dict]:
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    q = urlencode(
        {
            "dataInicio": start.isoformat(),
            "dataFim": end.isoformat(),
            "itens": 50,
            "ordem": "DESC",
            "ordenarPor": "dataHoraRegistro",
        }
    )
    try:
        data = http_get_json(f"{CAMARA}/votacoes?{q}")
        return data.get("dados", [])[:20]
    except Exception as e:  # noqa: BLE001
        log.warning("votacoes camara: %s", e)
        return []


def fetch_votos_votacao(votacao_id: str) -> list[dict]:
    try:
        data = http_get_json(f"{CAMARA}/votacoes/{votacao_id}/votos")
        return data.get("dados", [])
    except Exception as e:  # noqa: BLE001
        log.debug("votos %s: %s", votacao_id, e)
        return []


# ---------- Scoring ----------

def score_from_activity(n_votos: int, presentes: int, gasto: float, teto: float = 45000.0) -> dict:
    """Simple transparent scoring; flag dados_insuficientes when empty."""
    if n_votos == 0 and gasto <= 0:
        return {
            "score_geral": 30.0,
            "score_atividade": 30.0,
            "score_gasto": 30.0,
            "score_transparencia": 50.0,
            "score_coerencia": 50.0,
            "dados_insuficientes": 1,
            "detalhes": {"n_votos": 0, "gasto": gasto, "dados_insuficientes": True},
        }
    presenca = 100.0 * presentes / max(n_votos, 1) if n_votos else 40.0
    economia = max(0.0, min(100.0, 100.0 * (1.0 - min(gasto, teto) / teto)))
    # placeholder transparency / coherence until TSE/orientation enrichment
    transp = 55.0
    coerencia = 55.0
    geral = round(0.35 * presenca + 0.35 * economia + 0.15 * transp + 0.15 * coerencia, 2)
    return {
        "score_geral": geral,
        "score_atividade": round(presenca, 2),
        "score_gasto": round(economia, 2),
        "score_transparencia": transp,
        "score_coerencia": coerencia,
        "dados_insuficientes": 0,
        "detalhes": {"n_votos": n_votos, "presentes": presentes, "gasto": gasto},
    }


def save_score(conn: sqlite3.Connection, pid: int, periodo: str, sc: dict) -> None:
    conn.execute(
        """
        INSERT INTO scores (
          parlamentar_id, periodo, score_geral, score_atividade, score_gasto,
          score_transparencia, score_coerencia, dados_insuficientes, detalhes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(parlamentar_id, periodo) DO UPDATE SET
          score_geral=excluded.score_geral,
          score_atividade=excluded.score_atividade,
          score_gasto=excluded.score_gasto,
          score_transparencia=excluded.score_transparencia,
          score_coerencia=excluded.score_coerencia,
          dados_insuficientes=excluded.dados_insuficientes,
          detalhes=excluded.detalhes
        """,
        (
            pid,
            periodo,
            sc["score_geral"],
            sc["score_atividade"],
            sc["score_gasto"],
            sc["score_transparencia"],
            sc["score_coerencia"],
            sc["dados_insuficientes"],
            json.dumps(sc["detalhes"], ensure_ascii=False),
        ),
    )


def save_despesa(conn: sqlite3.Connection, pid: int, periodo: str, d: dict) -> None:
    valor = float(d.get("valorDocumento") or d.get("valorLiquido") or 0)
    data_doc = d.get("dataDocumento") or d.get("data")
    cnpj = (d.get("cnpjCpfFornecedor") or "").strip()
    fornecedor = d.get("nomeFornecedor") or d.get("fornecedor")
    tipo = d.get("tipoDespesa") or d.get("tipo")
    url = d.get("urlDocumento")
    chave = f"{pid}|{periodo}|{data_doc}|{cnpj}|{valor}|{tipo}|{fornecedor}"
    conn.execute(
        """
        INSERT INTO despesas (
          parlamentar_id, periodo, tipo_despesa, fornecedor, cnpj_fornecedor,
          data_documento, valor, url_documento, chave_natural
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(chave_natural) DO UPDATE SET valor=excluded.valor, url_documento=excluded.url_documento
        """,
        (pid, periodo, tipo, fornecedor, cnpj, data_doc, valor, url, chave),
    )


# ---------- CNPJ enrichment (cache + optional BrasilAPI) ----------

def enrich_cnpj(conn: sqlite3.Connection, cnpj: str) -> None:
    digits = "".join(ch for ch in (cnpj or "") if ch.isdigit())
    if len(digits) != 14:
        return
    existing = conn.execute("SELECT cnpj FROM fornecedores WHERE cnpj=?", (digits,)).fetchone()
    if existing:
        return
    situacao = "NAO_VERIFICADO"
    detalhe = None
    try:
        data = http_get_json(f"https://brasilapi.com.br/api/cnpj/v1/{digits}")
        situacao = (data.get("descricao_situacao_cadastral") or data.get("situacao_cadastral") or "ATIVA")
        if isinstance(situacao, int):
            situacao = "ATIVA" if situacao == 2 else "IRREGULAR"
        situacao = str(situacao).upper()
        if "INAPT" in situacao or "BAIXAD" in situacao or "SUSPENS" in situacao:
            situacao = "IRREGULAR"
        detalhe = data.get("razao_social")
        time.sleep(0.25)
    except Exception as e:  # noqa: BLE001
        detalhe = f"falha consulta: {e}"
    conn.execute(
        """
        INSERT INTO fornecedores (cnpj, razao_social, situacao, sancionado_tcu, detalhe, verificado_em)
        VALUES (?, ?, ?, 0, ?, ?)
        ON CONFLICT(cnpj) DO UPDATE SET situacao=excluded.situacao, detalhe=excluded.detalhe, verificado_em=excluded.verificado_em
        """,
        (digits, detalhe, situacao, detalhe, datetime.utcnow().isoformat()),
    )


# ---------- Coherence stub (orientation when available) ----------

def mark_vote_divergence(voto: str, orientacao: str | None) -> int:
    if not orientacao:
        return 0
    o = orientacao.strip().lower()
    v = (voto or "").strip().lower()
    if not o or o in ("liberado", "obstrução", "obstrucao"):
        return 0
    from etl.coerencia_partidaria import divergiu

    return 1 if divergiu(voto, orientacao) else 0


# ---------- Migrate to Supabase ----------

def migrate_supabase(conn: sqlite3.Connection) -> None:
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        log.warning("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY ausentes — pulando migração.")
        return

    def upsert(table: str, rows: list[dict], on_conflict: str) -> None:
        if not rows:
            log.info("[%s] Tabela vazia localmente. Pulando.", table)
            return
        batch = 100
        total = (len(rows) + batch - 1) // batch
        log.info("[%s] Encontrados %s registros. Iniciando migração…", table, len(rows))
        for i in range(0, len(rows), batch):
            chunk = rows[i : i + batch]
            endpoint = f"{url}/rest/v1/{table}?on_conflict={on_conflict}"
            body = json.dumps(chunk).encode("utf-8")
            req = Request(
                endpoint,
                data=body,
                headers={
                    "apikey": key,
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "Prefer": "resolution=merge-duplicates,return=minimal",
                },
                method="POST",
            )
            try:
                with urlopen(req, timeout=120) as resp:
                    resp.read()
                log.info("[%s] Lote %s/%s migrado com sucesso.", table, i // batch + 1, total)
            except Exception as e:  # noqa: BLE001
                log.error("[%s] Falha lote %s: %s", table, i // batch + 1, e)
                raise

    def rows(sql: str) -> list[dict]:
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    # Map local ids — for supabase we send natural keys where possible.
    # Prefer uploading with stable business keys already in UNIQUE constraints.
    parl = rows(
        "SELECT id, id_api, nome_parlamentar, partido, uf, casa FROM parlamentares"
    )
    # Supabase expects id as PK — keep local ids if previously synced; else let serial assign via upsert on (casa,id_api)
    # PostgREST upsert on UNIQUE(casa,id_api) requires those columns; omit serial id for insert-or-merge by conflict target.
    parl_payload = [
        {"id_api": r["id_api"], "nome_parlamentar": r["nome_parlamentar"], "partido": r["partido"], "uf": r["uf"], "casa": r["casa"]}
        for r in parl
    ]
    upsert("parlamentares", parl_payload, "casa,id_api")

    # Re-fetch remote ids would be ideal; for local-first demo we also push scores with parlamentar_id local.
    # Production: resolve remote IDs via select. Here we push using id_api join on client views.
    # Simplified: push scores keyed by (parlamentar_id, periodo) using local ids only if DB was seeded from same source.
    scores = rows(
        """SELECT parlamentar_id, periodo, score_geral, score_atividade, score_gasto,
                  score_transparencia, score_coerencia, dados_insuficientes, detalhes
           FROM scores"""
    )
    for s in scores:
        if isinstance(s.get("detalhes"), str):
            try:
                s["detalhes"] = json.loads(s["detalhes"])
            except json.JSONDecodeError:
                s["detalhes"] = {}
        s["dados_insuficientes"] = bool(s.get("dados_insuficientes"))
    upsert("scores", scores, "parlamentar_id,periodo")

    despesas = rows(
        """SELECT parlamentar_id, periodo, tipo_despesa, fornecedor, cnpj_fornecedor,
                  data_documento, valor, url_documento, chave_natural FROM despesas"""
    )
    upsert("despesas", despesas, "chave_natural")

    votacoes = rows("SELECT id_api, casa, descricao, data FROM votacoes")
    upsert("votacoes", votacoes, "casa,id_api")

    votos = rows(
        "SELECT votacao_id, parlamentar_id, voto, descricao_ausencia, divergiu_orientacao FROM votos"
    )
    for v in votos:
        v["divergiu_orientacao"] = bool(v.get("divergiu_orientacao"))
    upsert("votos", votos, "votacao_id,parlamentar_id")

    props = rows(
        """SELECT parlamentar_id, casa, id_api, sigla_tipo, numero, ano, ementa, situacao, url_oficial
           FROM proposicoes"""
    )
    upsert("proposicoes", props, "casa,id_api")

    tse = rows(
        """SELECT parlamentar_id, nome_parlamentar, cargo, ano_eleicao, total_bens, total_receitas
           FROM candidaturas_tse"""
    )
    upsert("candidaturas_tse", tse, "parlamentar_id,ano_eleicao")

    forn = rows("SELECT cnpj, razao_social, situacao, sancionado_tcu, detalhe, verificado_em FROM fornecedores")
    for f in forn:
        f["sancionado_tcu"] = bool(f.get("sancionado_tcu"))
    upsert("fornecedores", forn, "cnpj")

    run = [{"atualizado_em": datetime.utcnow().isoformat() + "Z", "status": "ok", "detalhes": {"source": "etl"}}]
    upsert("pipeline_runs", run, "id")
    log.info("Migração concluída!")


# ---------- Main pipeline ----------

def collect_and_score(conn: sqlite3.Connection, n_months: int) -> None:
    periods = months_back(n_months)
    log.info("INICIANDO COLETA DINÂMICA (Baseada em: %s)", date.today().strftime("%d/%m/%Y"))
    deps = fetch_deputados()
    sens = fetch_senadores()

    # Deduplicate by (casa, id_api)
    seen: set[tuple[str, str]] = set()
    people: list[tuple[str, dict]] = []
    for d in deps:
        key = ("camara", str(d.get("id")))
        if key in seen or not d.get("id"):
            continue
        seen.add(key)
        people.append(("camara", d))
    for s in sens:
        key = ("senado", str(s.get("id")))
        if key in seen or not s.get("id"):
            continue
        seen.add(key)
        people.append(("senado", s))

    log.info("Total a processar (deduplicado): %s parlamentares.", len(people))

    # Register all
    id_map: dict[tuple[str, str], int] = {}
    for casa, raw in people:
        if casa == "camara":
            pid = upsert_parlamentar(
                conn,
                "camara",
                str(raw["id"]),
                raw.get("nome") or raw.get("nomeCivil") or "?",
                (raw.get("siglaPartido") or ""),
                (raw.get("siglaUf") or ""),
            )
        else:
            pid = upsert_parlamentar(
                conn,
                "senado",
                str(raw["id"]),
                raw.get("nome") or "?",
                raw.get("partido") or "",
                raw.get("uf") or "",
            )
        id_map[(casa, str(raw["id"]))] = pid
    conn.commit()

    # Votações Câmara por período (shared)
    votos_por_dep: dict[str, list[tuple[str, str, int]]] = {}  # id_api -> [(voto, orient, div)]
    for y, m in periods:
        periodo = period_label(y, m)
        log.info("PROCESSANDO PERÍODO: %s", periodo)
        votacoes = fetch_votacoes_camara(y, m)
        for vt in votacoes:
            vid = str(vt.get("id"))
            conn.execute(
                """
                INSERT INTO votacoes (id_api, casa, descricao, data)
                VALUES (?, 'camara', ?, ?)
                ON CONFLICT(casa, id_api) DO UPDATE SET descricao=excluded.descricao, data=excluded.data
                """,
                (vid, vt.get("descricao") or vt.get("aprovacao") or vid, vt.get("dataHoraRegistro") or vt.get("data")),
            )
            row = conn.execute("SELECT id FROM votacoes WHERE casa='camara' AND id_api=?", (vid,)).fetchone()
            local_vid = int(row["id"])
            orient = None
            for voto_row in fetch_votos_votacao(vid):
                dep = voto_row.get("deputado_") or voto_row.get("deputado") or {}
                dep_id = str(dep.get("id") or "")
                if not dep_id:
                    continue
                voto = voto_row.get("tipoVoto") or voto_row.get("voto") or ""
                div = mark_vote_divergence(voto, orient)
                pid = id_map.get(("camara", dep_id))
                if not pid:
                    continue
                conn.execute(
                    """
                    INSERT INTO votos (votacao_id, parlamentar_id, voto, descricao_ausencia, divergiu_orientacao)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(votacao_id, parlamentar_id) DO UPDATE SET voto=excluded.voto, divergiu_orientacao=excluded.divergiu_orientacao
                    """,
                    (local_vid, pid, voto, None, div),
                )
                votos_por_dep.setdefault(dep_id, []).append((periodo, voto, div))
        conn.commit()

    # Per parlamentar expenses + scores + proposicoes
    rejected = 0
    for idx, (casa, raw) in enumerate(people, 1):
        id_api = str(raw["id"] if casa == "camara" else raw["id"])
        nome = raw.get("nome") or raw.get("nomeCivil") or "?"
        pid = id_map[(casa, id_api)]
        for y, m in periods:
            periodo = period_label(y, m)
            desps = []
            if casa == "camara":
                desps = fetch_despesas_deputado(id_api, y, m)
                for d in desps:
                    save_despesa(conn, pid, periodo, d)
                    cnpj = d.get("cnpjCpfFornecedor")
                    if cnpj:
                        try:
                            enrich_cnpj(conn, str(cnpj))
                        except Exception:  # noqa: BLE001
                            pass
                # proposicoes once per year
                if m == periods[-1][1] or m == 1:
                    for pr in fetch_proposicoes_autor(id_api, y):
                        conn.execute(
                            """
                            INSERT INTO proposicoes (
                              parlamentar_id, casa, id_api, sigla_tipo, numero, ano, ementa, situacao, url_oficial
                            ) VALUES (?, 'camara', ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(casa, id_api) DO UPDATE SET ementa=excluded.ementa, situacao=excluded.situacao
                            """,
                            (
                                pid,
                                str(pr.get("id")),
                                pr.get("siglaTipo"),
                                str(pr.get("numero") or ""),
                                pr.get("ano"),
                                pr.get("ementa"),
                                (pr.get("statusProposicao") or {}).get("descricaoSituacao")
                                if isinstance(pr.get("statusProposicao"), dict)
                                else None,
                                pr.get("uri") or f"https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao={pr.get('id')}",
                            ),
                        )

            gasto = 0.0
            for d in desps:
                gasto += float(d.get("valorDocumento") or d.get("valorLiquido") or 0)
            vlist = [v for v in votos_por_dep.get(id_api, []) if v[0] == periodo]
            n_votos = len(vlist)
            presentes = sum(1 for v in vlist if (v[1] or "").lower() not in ("ausente", "não votou", "nao votou"))
            # coherence from divergences
            if n_votos:
                diverg = sum(1 for v in vlist if v[2])
                coer = max(0.0, 100.0 * (1.0 - diverg / n_votos))
            else:
                coer = 50.0
            sc = score_from_activity(n_votos, presentes, gasto)
            sc["score_coerencia"] = round(coer, 2)
            sc["detalhes"]["score_coerencia"] = coer
            save_score(conn, pid, periodo, sc)
        conn.commit()
        if idx % 50 == 0:
            log.info("[%s/%s] Processados…", idx, len(people))
        # light rate-limit
        if idx % 20 == 0:
            time.sleep(0.5)

    log.info("Coleta finalizada. Rejeitados/duplicados evitados na lista: %s", rejected)


def main() -> int:
    sqlite_path = os.getenv("SQLITE_PATH", str(ROOT / "candidatos.db"))
    n_months = int(os.getenv("ETL_MONTHS", "6"))
    conn = init_db(sqlite_path)
    log.info("=" * 60)
    log.info("INICIANDO PIPELINE AUTOMATIZADO CVP-IA")
    log.info("=" * 60)
    try:
        log.info("[1. Coleta e Scoring] INICIANDO…")
        collect_and_score(conn, n_months)
        log.info("[1. Coleta e Scoring] CONCLUÍDO.")
        log.info("[2. Auditoria local] (heurísticas no SQL Supabase / painel)")
        log.info("[3. Nuvem (Supabase)] INICIANDO…")
        migrate_supabase(conn)
        log.info("[3. Nuvem (Supabase)] CONCLUÍDO.")
        log.info("PIPELINE CVP-IA FINALIZADO.")
        return 0
    except Exception as e:  # noqa: BLE001
        log.exception("FALHA no pipeline: %s", e)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
