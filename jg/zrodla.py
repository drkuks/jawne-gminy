"""Source URLs used by the pipeline (details and verification results: sources.yaml)."""

from __future__ import annotations

# S1: MF Słownik JST (local government unit dictionary) by year ("Bazy danych" pages).
MF_SLOWNIK_JST = {
    2025: "https://www.gov.pl/attachment/737c9fd5-8f45-4424-8f8d-9fd5a2e656fe",
}

# S1: full-year reports ("po IV kwartałach") by year (the year's "Bazy danych" page).
MF_RB = {
    2025: {
        "Rb27S": "https://www.gov.pl/attachment/f98752f2-176a-4be1-910f-8aca6ffd9044",
        "Rb28S": "https://www.gov.pl/attachment/1eb59fb5-ab95-4f0d-8d62-4ef07c1a9910",
        "RbNDS": "https://www.gov.pl/attachment/580658fc-4575-4e4e-a1ca-a7478eb11149",
        "RbZ": "https://www.gov.pl/attachment/7ed5e4aa-50a5-46f7-a32e-337675319a8b",
    },
}

# S2: GUS BDL, population (variable 72305) – years fetched in one request per page.
BDL_LATA = list(range(2019, 2026))

# S4: PRG WFS, gmina boundaries layer.
PRG_WFS = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/PRG/WFS/AdministrativeBoundaries"
PRG_GMINY_PAGE = 100


def prg_gminy_url(start: int, count: int = PRG_GMINY_PAGE) -> str:
    return (
        f"{PRG_WFS}?service=WFS&version=2.0.0&request=GetFeature&typeNames=ms:A03_Granice_gmin"
        f"&count={count}&startIndex={start}&sortBy=JPT_KOD_JE"
    )
