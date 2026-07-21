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
    return f"{y}-{m:02d}"


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
    from etl.coerencia_partidaria import divergiu

    return 1 if divergiu(voto, orientacao) else 0


# ---------- Migrate to Supabase (LIVE UUID schema) ----------

def _rest(url: str, key: str, method: str, path: str, body: Any = None, prefer: str = "return=representation") -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = Request(
        f"{url}/rest/v1/{path}",
        data=data,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": prefer,
        },
        method=method,
    )
    with urlopen(req, timeout=180) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else None


def migrate_supabase(conn: sqlite3.Connection) -> None:
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        log.warning("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY ausentes — pulando migração.")
        return

    def upsert(table: str, rows_data: list[dict], on_conflict: str) -> None:
        if not rows_data:
            log.info("[%s] Vazio. Pulando.", table)
            return
        batch = 100
        total = (len(rows_data) + batch - 1) // batch
        log.info("[%s] %s registros…", table, len(rows_data))
        for i in range(0, len(rows_data), batch):
            chunk = rows_data[i : i + batch]
            endpoint = f"{table}?on_conflict={on_conflict}"
            try:
                _rest(url, key, "POST", endpoint, chunk, prefer="resolution=merge-duplicates,return=minimal")
                log.info("[%s] Lote %s/%s ok", table, i // batch + 1, total)
            except Exception as e:  # noqa: BLE001
                log.error("[%s] Falha lote %s: %s", table, i // batch + 1, e)
                raise

    def rows(sql: str) -> list[dict]:
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def fetch_all(table: str, select: str) -> list[dict]:
        out: list[dict] = []
        start = 0
        page = 1000
        while True:
            path = f"{table}?select={select}"
            req = Request(
                f"{url}/rest/v1/{path}",
                headers={
                    "apikey": key,
                    "Authorization": f"Bearer {key}",
                    "Range": f"{start}-{start + page - 1}",
                },
            )
            with urlopen(req, timeout=120) as resp:
                chunk = json.loads(resp.read().decode("utf-8") or "[]")
            if not chunk:
                break
            out.extend(chunk)
            if len(chunk) < page:
                break
            start += page
        return out

    # --- Parlamentares: upsert by id_camara / id_senado ---
    local_parl = rows("SELECT id, id_api, nome_parlamentar, partido, uf, casa FROM parlamentares")
    camara_rows = []
    senado_rows = []
    for r in local_parl:
        base = {
            "nome_parlamentar": r["nome_parlamentar"],
            "nome_civil": r["nome_parlamentar"],
            "partido": r["partido"],
            "uf": (r["uf"] or "")[:2] or None,
            "casa": r["casa"],
        }
        if r["casa"] == "camara":
            try:
                base["id_camara"] = int(r["id_api"])
            except (TypeError, ValueError):
                continue
            camara_rows.append(base)
        else:
            try:
                base["id_senado"] = int(r["id_api"])
            except (TypeError, ValueError):
                continue
            senado_rows.append(base)

    upsert("parlamentares", camara_rows, "id_camara")
    upsert("parlamentares", senado_rows, "id_senado")

    remote = fetch_all("parlamentares", "id,casa,id_camara,id_senado")
    remote_map: dict[tuple[str, str], str] = {}
    for r in remote:
        if r.get("id_camara") is not None:
            remote_map[("camara", str(r["id_camara"]))] = r["id"]
        if r.get("id_senado") is not None:
            remote_map[("senado", str(r["id_senado"]))] = r["id"]

    local_to_remote: dict[int, str] = {}
    for r in local_parl:
        rid = remote_map.get((r["casa"], str(r["id_api"])))
        if rid:
            local_to_remote[int(r["id"])] = rid
    log.info("Mapeados %s/%s parlamentares locais → UUID remoto", len(local_to_remote), len(local_parl))

    # --- Scores ---
    scores_payload = []
    for s in rows(
        """SELECT parlamentar_id, periodo, score_geral, score_atividade, score_gasto,
                  score_transparencia, score_coerencia, dados_insuficientes, detalhes FROM scores"""
    ):
        rid = local_to_remote.get(int(s["parlamentar_id"]))
        if not rid:
            continue
        det = s.get("detalhes")
        if isinstance(det, str):
            try:
                det = json.loads(det)
            except json.JSONDecodeError:
                det = {}
        scores_payload.append(
            {
                "parlamentar_id": rid,
                "periodo": s["periodo"],
                "score_geral": s["score_geral"],
                "score_atividade": s["score_atividade"],
                "score_gasto": s["score_gasto"],
                "score_transparencia": s["score_transparencia"],
                "score_coerencia": s["score_coerencia"],
                "dados_insuficientes": bool(s.get("dados_insuficientes")),
                "detalhes": det or {},
            }
        )
    upsert("scores", scores_payload, "parlamentar_id,periodo")

    # --- Despesas: replace by (ano, mes) window then insert ---
    desp_local = rows(
        """SELECT parlamentar_id, periodo, tipo_despesa, fornecedor, cnpj_fornecedor,
                  data_documento, valor, url_documento FROM despesas"""
    )
    periods = sorted({d["periodo"] for d in desp_local if d.get("periodo")})
    for per in periods:
        try:
            y, m = per.split("-")
            y_i, m_i = int(y), int(m)
        except Exception:  # noqa: BLE001
            continue
        # delete existing for period via REST filter
        del_path = f"despesas?ano=eq.{y_i}&mes=eq.{m_i}"
        try:
            _rest(url, key, "DELETE", del_path, prefer="return=minimal")
            log.info("[despesas] Limpou ano=%s mes=%s", y_i, m_i)
        except Exception as e:  # noqa: BLE001
            log.warning("[despesas] delete %s: %s", per, e)

    desp_payload = []
    for d in desp_local:
        rid = local_to_remote.get(int(d["parlamentar_id"]))
        if not rid or not d.get("periodo"):
            continue
        try:
            y, m = str(d["periodo"]).split("-")
            y_i, m_i = int(y), int(m)
        except Exception:  # noqa: BLE001
            continue
        desp_payload.append(
            {
                "parlamentar_id": rid,
                "ano": y_i,
                "mes": m_i,
                "tipo_despesa": d.get("tipo_despesa"),
                "fornecedor": d.get("fornecedor"),
                "cnpj_fornecedor": d.get("cnpj_fornecedor"),
                "data_documento": d.get("data_documento"),
                "valor": d.get("valor") or 0,
                "url_documento": d.get("url_documento") or "",
            }
        )
    # plain insert (no on_conflict) in batches
    if desp_payload:
        batch = 100
        total = (len(desp_payload) + batch - 1) // batch
        log.info("[despesas] Inserindo %s…", len(desp_payload))
        for i in range(0, len(desp_payload), batch):
            chunk = desp_payload[i : i + batch]
            _rest(url, key, "POST", "despesas", chunk, prefer="return=minimal")
            log.info("[despesas] Lote %s/%s ok", i // batch + 1, total)

    # --- Votações / votos ---
    vot_local = rows("SELECT id, id_api, casa, descricao, data FROM votacoes")
    vot_payload = [
        {
            "casa": v["casa"],
            "id_externo": v["id_api"],
            "descricao": v.get("descricao"),
            "data": (v.get("data") or "")[:10] or None,
        }
        for v in vot_local
        if v.get("id_api")
    ]
    upsert("votacoes", vot_payload, "casa,id_externo")

    remote_vot = fetch_all("votacoes", "id,casa,id_externo")
    vot_map = {(v["casa"], str(v["id_externo"])): v["id"] for v in remote_vot}

    local_vot_to_remote: dict[int, str] = {}
    for v in vot_local:
        rid = vot_map.get((v["casa"], str(v["id_api"])))
        if rid:
            local_vot_to_remote[int(v["id"])] = rid

    votos_payload = []
    for v in rows(
        "SELECT votacao_id, parlamentar_id, voto, descricao_ausencia, divergiu_orientacao FROM votos"
    ):
        vid = local_vot_to_remote.get(int(v["votacao_id"]))
        pid = local_to_remote.get(int(v["parlamentar_id"]))
        if not vid or not pid:
            continue
        votos_payload.append(
            {
                "votacao_id": vid,
                "parlamentar_id": pid,
                "voto": v.get("voto"),
                "descricao_ausencia": v.get("descricao_ausencia"),
                "divergiu_orientacao": bool(v.get("divergiu_orientacao")),
            }
        )
    upsert("votos", votos_payload, "votacao_id,parlamentar_id")

    # --- Proposições ---
    props_payload = []
    for p in rows(
        """SELECT parlamentar_id, casa, id_api, sigla_tipo, numero, ano, ementa, situacao, url_oficial
           FROM proposicoes"""
    ):
        pid = local_to_remote.get(int(p["parlamentar_id"])) if p.get("parlamentar_id") else None
        if not p.get("id_api") or not pid:
            continue
        props_payload.append(
            {
                "parlamentar_id": pid,
                "casa": p.get("casa"),
                "id_api": str(p["id_api"]),
                "tipo": p.get("sigla_tipo"),
                "sigla_tipo": p.get("sigla_tipo"),
                "numero": p.get("numero"),
                "ano": p.get("ano"),
                "ementa": p.get("ementa"),
                "situacao": p.get("situacao"),
                "url_oficial": p.get("url_oficial"),
            }
        )
    upsert("proposicoes", props_payload, "casa,id_api")

    # --- Fornecedores ---
    forn = rows("SELECT cnpj, razao_social, situacao, sancionado_tcu, detalhe, verificado_em FROM fornecedores")
    forn_payload = []
    for f in forn:
        digits = "".join(ch for ch in str(f.get("cnpj") or "") if ch.isdigit())
        if len(digits) != 14:
            continue
        forn_payload.append(
            {
                "cnpj": digits,
                "razao_social": f.get("razao_social"),
                "situacao": f.get("situacao"),
                "sancionado_tcu": bool(f.get("sancionado_tcu")),
                "detalhe": f.get("detalhe"),
                "verificado_em": f.get("verificado_em"),
            }
        )
    upsert("fornecedores", forn_payload, "cnpj")

    _rest(
        url,
        key,
        "POST",
        "pipeline_runs",
        [{"status": "ok", "detalhes": {"source": "etl", "at": datetime.utcnow().isoformat() + "Z"}}],
        prefer="return=minimal",
    )
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
                    # CNPJ enrichment deferred to end of run (rate limits)
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

    # Enriquecer amostra de CNPJs únicos
    cnps = conn.execute(
        """SELECT DISTINCT cnpj_fornecedor FROM despesas
           WHERE cnpj_fornecedor IS NOT NULL AND length(replace(replace(cnpj_fornecedor,'.',''),'/','')) >= 14
           LIMIT 80"""
    ).fetchall()
    log.info("Enriquecendo até %s CNPJs…", len(cnps))
    for row in cnps:
        try:
            enrich_cnpj(conn, str(row[0]))
        except Exception as e:  # noqa: BLE001
            log.debug("cnpj skip: %s", e)
    conn.commit()


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
