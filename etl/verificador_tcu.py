"""
Verificação de CNPJ / sanções (cache local).

Usa BrasilAPI para situação cadastral. Sanções TCU podem ser plugadas
via lista CSV/API oficial quando disponível (campo sancionado_tcu).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

CACHE_PATH = Path(__file__).resolve().parents[1] / "cnpj_cache.json"


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_cnpj(cnpj: str) -> str:
    return "".join(ch for ch in (cnpj or "") if ch.isdigit())


def consultar_cnpj(cnpj: str) -> dict[str, Any]:
    digits = normalize_cnpj(cnpj)
    if len(digits) != 14:
        return {"cnpj": digits, "situacao": "INVALIDO", "sancionado_tcu": False}
    cache = _load_cache()
    if digits in cache:
        return cache[digits]
    situacao = "NAO_VERIFICADO"
    razao = None
    try:
        req = Request(
            f"https://brasilapi.com.br/api/cnpj/v1/{digits}",
            headers={"Accept": "application/json", "User-Agent": "CVP-IA-ETL/1.0"},
        )
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        situacao = str(data.get("descricao_situacao_cadastral") or "ATIVA").upper()
        if any(x in situacao for x in ("INAPT", "BAIXAD", "SUSPENS")):
            situacao = "IRREGULAR"
        razao = data.get("razao_social")
        time.sleep(0.2)
    except Exception as e:  # noqa: BLE001
        situacao = "NAO_VERIFICADO"
        razao = str(e)
    row = {
        "cnpj": digits,
        "razao_social": razao,
        "situacao": situacao,
        "sancionado_tcu": False,
        "detalhe": razao,
        "verificado_em": datetime.now(timezone.utc).isoformat(),
    }
    cache[digits] = row
    _save_cache(cache)
    return row
