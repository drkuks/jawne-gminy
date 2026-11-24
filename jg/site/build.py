"""Build the static site from `data/*.csv` into `site/dist/`.

Every number on a page comes from a data row with document, position, fetch date and hash; the
page shows them next to the value or in the source documents section. A missing value is shown as
"brak danych" (no data) with a reason. The forbidden-word linter checks the generated HTML and
stops the build.
"""

from __future__ import annotations

import csv
import json
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from jg import format as fmt
from jg.budzet import BRAK, Wartosc, wczytaj
from jg.gminy import BRAK_UWAG, read_mf_slownik
from jg.lint import check_html
from jg.metrics import MetricsFile, load_metrics
from jg.site import charts, eksport

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "site" / "templates"
STATIC = ROOT / "site" / "static"
KPI = (
    ("dochody_wykonanie", "Dochody wykonane"),
    ("wydatki_wykonanie", "Wydatki wykonane"),
    ("wydatki_majatkowe_wykonanie", "Wydatki majątkowe wykonane"),
    ("zobowiazania_ogolem", "Zobowiązania ogółem (31 grudnia)"),
)


class BuildError(Exception):
    pass


MARKER = ".jg-serwis"


def _wyczysc(out: Path) -> None:
    """Remove the previous build output only if the directory is empty or has the generator
    marker (protects against `--wyjscie data` or `--wyjscie .`)."""
    if not out.exists():
        return
    if not out.is_dir():
        raise BuildError(f"{out} istnieje i nie jest katalogiem")
    if any(out.iterdir()) and not (out / MARKER).is_file():
        raise BuildError(f"{out} nie jest katalogiem serwisu (brak {MARKER}); nie usuwam")
    shutil.rmtree(out)


def tokens() -> dict:
    return json.loads((ROOT / "site" / "design-tokens.json").read_text(encoding="utf-8"))


def css_variables(t: dict) -> str:
    """CSS variables from the tokens (the only source of colours and sizes)."""
    lines = [f"  --{k}: {v};" for k, v in t["color"].items()]
    lines += [f"  --font-{k}: {v};" for k, v in t["font"].items()]
    for name, spec in t["type"].items():
        lines.append(f"  --{name}-size: {spec['size']}px;")
        lines.append(f"  --{name}-lh: {spec['lineHeight']};")
        lines.append(f"  --{name}-weight: {spec['weight']};")
    lines += [f"  --space-{k}: {v}px;" for k, v in t["space"].items()]
    lines += [f"  --radius-{k}: {v}px;" for k, v in t["radius"].items()]
    lines += [
        f"  --layout-{k}: {v}{'' if k == 'columns' else 'px'};" for k, v in t["layout"].items()
    ]
    lines.append(f"  --touch: {t['touchTarget']}px;")
    return ":root {\n" + "\n".join(lines) + "\n}\n"


def _csv(path: Path) -> list[dict]:
    if not path.is_file():
        raise BuildError(f"brak {path}: uruchom potok danych (jg gminy, jg seria, …)")
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _dec(text: str) -> Decimal | None:
    return None if not text or text.startswith(BRAK) else Decimal(text)


@dataclass
class Gmina:
    teryt: str
    nazwa: str
    typ: str
    mnpp: bool
    powiat: str
    woj: str
    lat: float | None
    lon: float | None
    uwagi: str
    wartosci: dict[tuple[int, str], Wartosc] = field(default_factory=dict)
    porownania: list[dict] = field(default_factory=list)
    spolki: list[dict] = field(default_factory=list)

    @property
    def typ_opis(self) -> str:
        return "miasto na prawach powiatu" if self.mnpp else f"gmina {self.typ}"

    def w(self, rok: int, kod: str) -> Wartosc | None:
        return self.wartosci.get((rok, kod))


@dataclass
class Model:
    gminy: list[Gmina]
    lata: list[int]
    rok: int
    metrics: MetricsFile
    dokumenty: list[dict]
    spolki_n: int
    aktualizacja: str
    strips: dict[tuple[str, str], dict]  # (code, group) → statistics and SVG file


def _nazwy_jednostek(archive_dir: Path, sha: str) -> tuple[dict[str, str], dict[str, str]]:
    """Powiat (WWPP) and województwo (WW) names from the MF JST dictionary used for gminy.csv."""
    path = archive_dir / sha[:2] / sha
    if not path.is_file():
        raise BuildError(f"brak Słownika JST w archiwum ({sha[:12]}…): uruchom `jg gminy`")
    powiaty, woj = {}, {}
    for r in read_mf_slownik(path.read_bytes()):
        if r.pk == "00" and r.gk == "00" and r.gt == "0":
            woj[r.wk] = r.nazwa
        elif r.gk == "00" and r.gt == "0" and r.pt in ("1", "2"):
            powiaty[r.wk + r.pk] = r.nazwa
    return powiaty, woj


def load(data: Path, archive_dir: Path) -> Model:
    gminy_rows = _csv(data / "gminy.csv")
    if not gminy_rows:
        raise BuildError(f"{data / 'gminy.csv'} bez wierszy: uruchom `jg gminy`")
    powiaty, woj = _nazwy_jednostek(archive_dir, gminy_rows[0]["zrodlo_mf_sha256"])
    gminy = {
        r["teryt"]: Gmina(
            r["teryt"],
            r["nazwa"],
            r["typ"],
            r["mnpp"] == "1",
            powiaty.get(r["powiat_teryt"], BRAK),
            woj.get(r["woj_teryt"], BRAK),
            None if r["lat"].startswith(BRAK) else float(r["lat"]),
            None if r["lon"].startswith(BRAK) else float(r["lon"]),
            "" if r["uwagi"] == BRAK_UWAG else r["uwagi"],
        )
        for r in gminy_rows
    }
    wartosci = wczytaj(data / "wartosci.csv")
    if not wartosci:
        raise BuildError(f"{data / 'wartosci.csv'} bez wierszy: uruchom `jg seria`")
    for w in wartosci:
        if w.teryt in gminy:
            gminy[w.teryt].wartosci[(w.rok, w.kod)] = w
    kpi_kody = {k for k, _ in KPI}
    lata = sorted({w.rok for w in wartosci if w.kod in kpi_kody and w.status == "ok"})
    if not lata:
        raise BuildError("brak wartości budżetu ze statusem ok w data/wartosci.csv")
    for r in _csv(data / "porownania.csv"):
        if r["teryt"] in gminy and int(r["rok"]) == lata[-1]:
            gminy[r["teryt"]].porownania.append(r)
    spolki = [r for r in _csv(data / "spolki.csv") if r["komunalna"] == "1"]
    finanse = defaultdict(dict)
    finanse_csv = data / "spolki_finanse.csv"
    # Without the file (CI run, no manual RDF files) company finances are "brak danych" + reason.
    for r in _csv(finanse_csv) if finanse_csv.is_file() else []:
        finanse[r["krs"]][(int(r["rok"]), r["kod"])] = r
    udzialy = defaultdict(list)
    for r in _csv(data / "spolki_wspolnicy.csv"):
        if r["do"] == "obecnie":
            udzialy[r["krs"]].append(r)
    for s in spolki:
        s["finanse"] = finanse.get(s["krs"], {})
        s["wspolnicy"] = udzialy.get(s["krs"], [])
        # A company appears on the page of every gmina that is a current shareholder; a subsidiary
        # of a municipal company (no gmina among shareholders) on the gmina found via the parent.
        teryty = {w["wspolnik_teryt"] for w in s["wspolnicy"] if w["wspolnik_teryt"] in gminy}
        for t in teryty or {s["gmina_ustalona"]}:
            if t in gminy:
                gminy[t].spolki.append(s)
    manifest = archive_dir / "manifest.jsonl"
    docs = []
    if manifest.is_file():
        seen = set()
        for line in reversed(manifest.read_text(encoding="utf-8").splitlines()):
            rec = json.loads(line)
            if rec["sha256"] in seen or not rec.get("note") or rec["source_id"] not in ("S1", "S6"):
                continue
            seen.add(rec["sha256"])
            docs.append({**rec, "tytul": _tytul(rec)})
    metrics = load_metrics(ROOT / "metrics.yaml")
    aktualizacja = max((w.pobrano for w in wartosci if w.pobrano != BRAK), default=BRAK)
    return Model(
        sorted(gminy.values(), key=lambda g: g.teryt),
        lata,
        lata[-1],
        metrics,
        docs[:6],
        len(spolki),
        aktualizacja,
        {},
    )


def _strips(model: Model, colors: dict, out: Path) -> None:
    """Shared group dot strips for the latest year (one file per metric and group)."""
    by: dict[tuple[str, str], list[Decimal]] = defaultdict(list)
    for g in model.gminy:
        for r in g.porownania:
            if r["status"] == "ok":
                by[(r["kod"], r["grupa"])].append(Decimal(r["wartosc"]))
    (out / "grupy").mkdir(parents=True, exist_ok=True)
    for i, ((kod, grupa), values) in enumerate(sorted(by.items())):
        name = f"grupy/{model.rok}-{i:03d}.svg"
        label = f"{grupa}: {len(values)} gmin, wartości od najniższej do najwyższej"
        (out / name).write_text(
            charts.dot_strip(values, colors["ink-2"], colors["line"], label), encoding="utf-8"
        )
        model.strips[(kod, grupa)] = {"plik": name, "lo": min(values), "hi": max(values)}


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters.update(fmt.FILTERS)
    env.filters["status_brak"] = lambda s: s if s.startswith(BRAK) else f"{BRAK}: {s}"
    env.filters["wz"] = _wz
    return env


def build(
    data: Path, archive_dir: Path, out: Path, limit: list[str] | None = None, baza: str = "/"
) -> dict:
    """Build the site. `limit`: only selected gminy (pages, list, search – a preview without
    dead links). `baza`: site root path for the 404 page."""
    model = load(data, archive_dir)
    if limit:
        model.gminy = [g for g in model.gminy if g.teryt in set(limit)]
    t = tokens()
    colors = t["color"]
    _wyczysc(out)
    (out / "static").mkdir(parents=True)
    (out / MARKER).write_text("katalog wygenerowany przez jg serwis\n", encoding="utf-8")
    shutil.copytree(STATIC / "fonts", out / "static" / "fonts")
    for f in STATIC.glob("*.*"):
        shutil.copy(f, out / "static" / f.name)
    (out / "static" / "tokens.css").write_text(css_variables(t), encoding="utf-8")
    points = [(g.lat, g.lon) for g in model.gminy if g.lat is not None]
    (out / "static" / "mapa-ciemna.svg").write_text(
        charts.dot_map(points, colors["accent-on-dark"], 1.4), encoding="utf-8"
    )
    (out / "static" / "mapa.svg").write_text(
        charts.dot_map(points, colors["accent-soft"], 1.4), encoding="utf-8"
    )
    _strips(model, colors, out)
    env = _env()
    metryki = {m.id: m for m in model.metrics.metrics}
    common = {"model": model, "metryki": metryki, "BRAK": BRAK, "charts": charts, "colors": colors}
    pages: dict[str, str] = {}
    pages["index.html"] = env.get_template("index.html").render(
        root="",
        **common,
        wyrozniona=_by(model, "0215011"),
        ze_spolkami=[g for g in model.gminy if g.spolki],
    )
    pages["gminy.html"] = env.get_template("gminy.html").render(root="", **common)
    pages["metodologia.html"] = env.get_template("metodologia.html").render(
        root="", **common, zrodla=yaml.safe_load((ROOT / "sources.yaml").read_text("utf-8"))
    )
    try:
        pliki = eksport.eksportuj(data, out, {g.teryt for g in model.gminy})
    except eksport.EksportError as exc:
        raise BuildError(str(exc)) from exc
    pages["dane.html"] = env.get_template("dane.html").render(
        root="",
        **common,
        pliki=pliki,
        pola=eksport.POLA,
        rozmiar=eksport.rozmiar,
        zrodla_licencja=eksport.LICENCJA_ZRODLA,
    )
    # 404 is served at any URL: paths are absolute from the site root.
    pages["404.html"] = env.get_template("404.html").render(root=baza, **common)
    tpl = env.get_template("gmina.html")
    for g in model.gminy:
        if limit and g.teryt not in limit:
            continue
        pages[f"gmina/{g.teryt}.html"] = tpl.render(
            root="../", g=g, **common, karta=_karta(g, model, colors)
        )
    findings = []
    for name, html in pages.items():
        findings += check_html(html, name)
    if findings:
        raise BuildError(
            "słowa zakazane: " + "; ".join(f"{f.path}:{f.line} {f.word}" for f in findings[:10])
        )
    for name, html in pages.items():
        (out / name).parent.mkdir(parents=True, exist_ok=True)
        (out / name).write_text(html, encoding="utf-8")
    index = [
        {
            "n": g.nazwa,
            "t": g.typ_opis,
            "p": "" if g.mnpp else g.powiat,
            "w": g.woj,
            "u": f"gmina/{g.teryt}.html",
        }
        for g in model.gminy
    ]
    (out / "szukaj.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    return {"strony": len(pages), "gminy": len(model.gminy), "rok": model.rok}


def _wz(w: Wartosc | None, rodzaj: str = "rounded") -> str:
    """Value of a fact, or "brak danych: <reason>" (the data row status)."""
    if w is None:
        return f"{BRAK}: brak wartości w danych"
    if w.status != "ok" or w.wartosc is None:
        return w.status if w.status.startswith(BRAK) else f"{BRAK}: {w.status}"
    return fmt.FILTERS[rodzaj](w.wartosc)


def _tytul(rec: dict) -> str:
    """Reader-facing document title: manual RDF file → "Sprawozdanie finansowe … KRS …, year";
    MF file → description from the manifest ("Rb27S 2025 IV kw.: Dochody po IV kwartałach …")."""
    url = rec.get("url", "")
    if url.startswith("manual:rdf/"):
        parts = url.split("/")
        rok = parts[2] if len(parts) > 3 and parts[2].isdigit() else ""
        return f"Sprawozdanie finansowe spółki, KRS {parts[1]}" + (f", {rok} r." if rok else "")
    note = rec.get("note") or ""
    return note.split(": ", 1)[1] if ": " in note else note


def _by(model: Model, teryt: str) -> Gmina | None:
    return next((g for g in model.gminy if g.teryt == teryt), None)


def _karta(g: Gmina, model: Model, colors: dict) -> dict:
    rok = model.rok
    kpi = []
    for kod, label in KPI:
        w = g.w(rok, kod)
        kpi.append({"kod": kod, "label": label, "w": w})
    plan = g.w(rok, "wydatki_majatkowe_plan")
    proc = g.w(rok, "wydatki_majatkowe_wykonanie_proc")
    slupki = [
        charts.Slupek(
            r, _ok(g.w(r, "wydatki_majatkowe_plan")), _ok(g.w(r, "wydatki_majatkowe_wykonanie"))
        )
        for r in model.lata
    ]
    wykres = charts.bars(
        slupki,
        colors,
        f"Wydatki majątkowe gminy {g.nazwa}: plan i wykonanie w mln zł, lata "
        f"{model.lata[0]}–{model.lata[-1]}",
    )
    por = []
    for r in g.porownania:
        strip = model.strips.get((r["kod"], r["grupa"]))
        pos = None
        if strip and r["status"] == "ok":
            pos = charts.strip_position(Decimal(r["wartosc"]), strip["lo"], strip["hi"])
        por.append(
            {
                **r,
                "strip": strip,
                "pos": pos,
                "wartosc_d": _dec(r["wartosc"]),
                "mediana_d": _dec(r["mediana"]),
                "q1_d": _dec(r["q1"]),
                "q3_d": _dec(r["q3"]),
            }
        )
    pos = charts.map_position(g.lat, g.lon) if g.lat is not None else None
    dokumenty = {}
    for w in g.wartosci.values():
        if w.zrodlo_sha256 not in (BRAK, "") and ";" not in w.zrodlo_sha256:
            dokumenty.setdefault(w.zrodlo_sha256, (w.dokument, w.pobrano, w.rok))
    for s in g.spolki:
        dokumenty.setdefault(
            s["zrodlo_sha256"], (f"KRS {s['krs']}, odpis pełny", s["pobrano"], rok)
        )
        for (r, _), f in s["finanse"].items():
            if f["status"] == "ok" and not f["zrodlo_sha256"].startswith(BRAK):
                dokumenty.setdefault(
                    f["zrodlo_sha256"], (f"KRS {s['krs']}: {f['dokument']}", f["pobrano"], r)
                )
    docs = sorted(dokumenty.items(), key=lambda kv: (-kv[1][2], kv[1][0]))
    return {
        "kpi": kpi,
        "plan": plan,
        "proc": proc,
        "slupki": slupki,
        "wykres": wykres,
        "porownania": por,
        "pozycja": pos,
        "dokumenty": docs,
        "ludnosc": g.w(rok, "ludnosc"),
    }


def _ok(w: Wartosc | None) -> Decimal | None:
    return w.wartosc if w is not None and w.status == "ok" else None
