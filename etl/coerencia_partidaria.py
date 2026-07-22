"""
Coerência partidária — utilitários para comparar voto x orientação.

Quando a API disponibilizar orientação do líder, marque divergiu_orientacao=True.
"""

from __future__ import annotations


def divergiu(voto: str | None, orientacao: str | None) -> bool:
    if not orientacao or not voto:
        return False
    o = orientacao.strip().lower()
    v = voto.strip().lower()
    if o in ("liberado", "obstrução", "obstrucao", "liberada"):
        return False
    sim = v in ("sim", "yes")
    nao = v in ("não", "nao", "no")
    o_sim = "sim" in o and "não" not in o and "nao" not in o
    o_nao = ("não" in o) or ("nao" in o) or o.startswith("opp")
    if o_sim and nao:
        return True
    if o_nao and sim:
        return True
    return False


def score_coerencia(votos: list[tuple[str, str | None]]) -> float:
    """votos: list of (voto, orientacao)."""
    if not votos:
        return 50.0
    usable = [(v, o) for v, o in votos if o]
    if not usable:
        return 55.0
    div = sum(1 for v, o in usable if divergiu(v, o))
    return round(100.0 * (1.0 - div / len(usable)), 2)
