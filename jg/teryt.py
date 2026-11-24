"""TERYT code of a gmina (municipality) from the unit codes in MF files (WK, PK, GK, GT, PT).
One rule for the gmina dictionary (`jg.gminy`) and the reports (`jg.mf_rb`)."""

from __future__ import annotations

TYPY = {"1": "miejska", "2": "wiejska", "3": "miejsko-wiejska"}


def gmina_teryt(wk: str, pk: str, gk: str, gt: str, pt: str) -> str | None:
    """Gmina code (7 characters), or None for a powiat, voivodeship or JST union.

    Gminy: GT = 1/2/3. Cities with powiat rights: powiat record GK = 00, GT = 0, PT = 2,
    gmina code WWPP011."""
    if gt in TYPY and gk != "00":
        return f"{wk}{pk}{gk}{gt}"
    if gk == "00" and gt == "0" and pt == "2":
        return f"{wk}{pk}011"
    return None


def is_mnpp(gk: str, gt: str, pt: str) -> bool:
    return gk == "00" and gt == "0" and pt == "2"


def kod_w_roku(
    teryt: str, w_pliku: set[str] | dict, slownik: set[str], zrodlo: str = "w pliku"
) -> tuple[str | None, str]:
    """Code under which a dictionary gmina appears in a file from another year, plus a note.

    A gmina that changed type (e.g. rural → urban-rural) has that year's code in older files:
    the same WWPPGG, a different type digit. Such a code is used only if it is the sole
    candidate and does not belong to another gmina in the dictionary."""
    if teryt in w_pliku:
        return teryt, ""
    inne = [
        teryt[:6] + t
        for t in TYPY
        if teryt[:6] + t in w_pliku and teryt[:6] + t != teryt and teryt[:6] + t not in slownik
    ]
    if len(inne) == 1:
        return inne[0], f"kod {zrodlo}: {inne[0]} (zmiana typu gminy)"
    return None, ""
