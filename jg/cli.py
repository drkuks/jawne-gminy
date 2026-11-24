"""`jg` commands: fetching, metric validation, language linter."""

from __future__ import annotations

from pathlib import Path

import typer

from jg.config import Settings

app = typer.Typer(no_args_is_help=True, help="Jawne Gminy: data tools")


@app.command()
def fetch(
    url: str, source: str = typer.Option(..., help="source id from sources.yaml, e.g. S1")
) -> None:
    """Fetch a file into the SHA-256 archive (rate limit, conditional cache, log)."""
    from jg.fetch import HttpClient

    with HttpClient(Settings.load(), source_id=source) as client:
        item = client.fetch_to_archive(url)
    typer.echo(f"{item.sha256}  {item.size} B  {item.path}")


@app.command("metrics-validate")
def metrics_validate(path: Path = Path("metrics.yaml")) -> None:
    """Check the schema and consistency of `metrics.yaml`."""
    from jg.metrics import load_metrics

    metrics = load_metrics(path)
    typer.echo(f"{path}: {len(metrics.metrics)} wskaźników, schemat poprawny")


@app.command()
def lint(paths: list[Path]) -> None:
    """Search text and HTML files for words on the project's forbidden (evaluative) word list."""
    from jg.lint import check_paths

    files: list[Path] = []
    for path in paths:
        files.extend(sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path])
    files = [f for f in files if f.suffix in (".html", ".htm", ".md", ".txt", ".yaml", ".csv")]
    findings = check_paths(files)
    for f in findings:
        typer.echo(f"{f.path}:{f.line}: „{f.word}” – {f.context}")
    if findings:
        raise typer.Exit(1)
    typer.echo(f"{len(files)} plików bez słów zakazanych")


@app.command()
def gminy(rok: int = 2025, wyjscie: Path = Path("data/gminy.csv")) -> None:
    """Gmina (municipality) list: MF JST list (codes, type) + PRG (name, point); CSV + report."""
    import csv

    from jg import gminy as gminy_mod
    from jg import potok
    from jg.fetch import HttpClient
    from jg.gminy import gminy_z_mf, number_returned, polacz, read_mf_slownik, read_prg_gml
    from jg.zrodla import MF_SLOWNIK_JST, PRG_GMINY_PAGE, prg_gminy_url

    settings = Settings.load()
    with HttpClient(settings, source_id="S1") as client:
        try:
            url = MF_SLOWNIK_JST.get(rok) or potok.slownik_url(client, rok)
        except potok.PotokError as exc:
            raise typer.BadParameter(f"Słownik JST {rok}: {exc}") from exc
        mf_file = client.fetch_to_archive(url, note=f"Słownik JST {rok}")
    mf = gminy_z_mf(read_mf_slownik(mf_file.read_bytes()))
    prg, prg_files, pominiete = [], [], []
    with HttpClient(settings, source_id="S4") as client:
        start = 0
        # Data ends only at a page with numberReturned = 0; a shorter page does not end the loop.
        while True:
            page = client.fetch_to_archive(prg_gminy_url(start), note="PRG A03_Granice_gmin")
            content = page.read_bytes()
            items, skipped = read_prg_gml(content, page.sha256)
            if number_returned(content) == 0:
                break
            prg.extend(items)
            pominiete.extend(skipped)
            prg_files.append(page)
            typer.echo(f"PRG od {start}: {len(items)} gmin")
            start += PRG_GMINY_PAGE
    lista, rozbieznosci = polacz(mf, prg)
    prg_dates = {f.sha256: f.fetched_at for f in prg_files}
    brak = "brak danych"
    wyjscie.parent.mkdir(parents=True, exist_ok=True)
    with wyjscie.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "teryt",
                "nazwa",
                "typ",
                "mnpp",
                "powiat_teryt",
                "woj_teryt",
                "regon",
                "lat",
                "lon",
                "nazwa_mf",
                "uwagi",
                "stan_mf",
                "zrodlo_mf_sha256",
                "pobrano_mf",
                "zrodlo_prg_sha256",
                "pobrano_prg",
            ]
        )
        for g in lista:
            w.writerow(
                [
                    g.teryt,
                    g.nazwa,
                    g.typ,
                    int(g.mnpp),
                    g.powiat_teryt,
                    g.woj_teryt,
                    g.regon or brak,
                    g.lat if g.lat is not None else brak,
                    g.lon if g.lon is not None else brak,
                    g.nazwa_mf,
                    "; ".join(g.uwagi) or gminy_mod.BRAK_UWAG,
                    rok,
                    mf_file.sha256,
                    mf_file.fetched_at,
                    g.zrodlo_prg_sha256 or brak,
                    prg_dates.get(g.zrodlo_prg_sha256 or "", brak),
                ]
            )
    raport = wyjscie.with_suffix(".raport.txt")
    problemy = pominiete + rozbieznosci
    raport.write_text(
        f"MF {rok} sha256 {mf_file.sha256} ({mf_file.fetched_at}): {len(mf)} gmin\n"
        f"PRG ({len(prg_files)} stron): {len(prg)} gmin\n"
        f"Rozbieżności: {len(problemy)}\n" + "\n".join(problemy) + "\n",
        encoding="utf-8",
    )
    typer.echo(f"{wyjscie}: {len(lista)} gmin; rozbieżności: {len(problemy)} ({raport})")


@app.command()
def budzet(rok: int = 2025, gminy_csv: Path = Path("data/gminy.csv")) -> None:
    """Gmina budgets from Rb-27S, Rb-28S and Rb-NDS (after Q4) with a balance check."""

    from jg import budzet as b
    from jg import mf_rb
    from jg.fetch import HttpClient
    from jg.zrodla import MF_RB

    if rok not in MF_RB:
        raise typer.BadParameter(f"brak adresów sprawozdań dla {rok}; są: {sorted(MF_RB)}")
    teryty = _gminy(gminy_csv)
    with HttpClient(Settings.load(), source_id="S1") as client:
        pliki = {
            rep: client.fetch_to_archive(MF_RB[rok][rep], note=f"{rep} {rok} IV kw.")
            for rep in ("Rb27S", "Rb28S", "RbNDS")
        }
    rb27 = mf_rb.sumy_rb(pliki["Rb27S"].path, "Rb27S")
    rb28 = mf_rb.sumy_rb(pliki["Rb28S"].path, "Rb28S")
    nds = mf_rb.rb_nds(pliki["RbNDS"].path)
    # The annual value comes only from the "after Q4" file for the given year, in all three.
    for nazwa, okres in (("Rb27S", rb27), ("Rb28S", rb28), ("RbNDS", nds)):
        if (okres.rok, okres.kwartal) != (rok, "4"):
            raise typer.BadParameter(
                f"{nazwa}: plik za {okres.rok}, kwartał {okres.kwartal}; oczekiwano {rok}, IV kw."
            )
    rows = b.wartosci(rok, teryty, pliki, rb27, rb28, nds.gminy)
    out = Path(f"data/budzet_{rok}.csv")
    b.zapisz(out, rows)
    b.zapisz_dzialy(
        Path(f"data/budzet_dzialy_{rok}.csv"), rok, teryty, pliki, rb27, rb28, b.status_ogolem(rows)
    )
    braki = [r for r in rows if r.status != "ok"]
    typer.echo(f"{out}: {len(rows)} wartości, brak danych: {len(braki)}")
    for r in braki[:20]:
        typer.echo(f"  {r.teryt} {r.kod}: {r.status}")


def _gminy(path: Path) -> list[str]:
    import csv

    if not path.is_file():
        raise typer.BadParameter(f"brak {path}: najpierw `jg gminy`")
    with path.open(encoding="utf-8") as fh:
        return [row["teryt"] for row in csv.DictReader(fh)]


@app.command()
def rbz(rok: int = 2025, gminy_csv: Path = Path("data/gminy.csv")) -> None:
    """Gmina liabilities from Rb-Z (after Q4) with sum checks."""
    from jg import budzet as b
    from jg import rb_z, wskazniki
    from jg.fetch import HttpClient
    from jg.zrodla import MF_RB

    teryty = _gminy(gminy_csv)
    if rok not in MF_RB or "RbZ" not in MF_RB[rok]:
        raise typer.BadParameter(f"brak adresu Rb-Z dla {rok}")
    with HttpClient(Settings.load(), source_id="S1") as client:
        src = client.fetch_to_archive(MF_RB[rok]["RbZ"], note=f"RbZ {rok} IV kw.")
    plik = rb_z.rb_z(src.path)
    if (plik.rok, plik.kwartal) != (rok, "4"):
        raise typer.BadParameter(f"Rb-Z: plik za {plik.rok}, kw. {plik.kwartal}")
    rows = wskazniki.wartosci_rbz(rok, teryty, plik, src)
    out = Path(f"data/zobowiazania_{rok}.csv")
    b.zapisz(out, rows)
    braki = [r for r in rows if r.status != "ok"]
    typer.echo(f"{out}: {len(rows)} wartości, brak danych: {len(braki)}")


@app.command()
def ludnosc(gminy_csv: Path = Path("data/gminy.csv")) -> None:
    """Gmina population from GUS BDL (variable 72305) for the years in `jg.zrodla.BDL_LATA`."""
    import json

    from jg import bdl, wskazniki
    from jg import budzet as b
    from jg.fetch import HttpClient
    from jg.zrodla import BDL_LATA

    teryty = _gminy(gminy_csv)
    settings = Settings.load()
    headers = {bdl.KEY_HEADER: settings.gus_bdl_key} if settings.gus_bdl_key else None
    dane, strony = [], []
    with HttpClient(settings, source_id="S2") as client:
        client.limiter.set_interval(bdl.BDL_HOST, bdl.BDL_INTERVAL_S)
        page, pages = 0, 1
        while page < pages:
            url = bdl.url_by_variable(bdl.ZMIENNA_LUDNOSC, BDL_LATA, page)
            src = client.fetch_to_archive(url, headers=headers, note="BDL 72305")
            data = json.loads(src.read_bytes())
            pages = bdl.total_pages(data)
            dane.extend(bdl.parse_page(data, len(strony)))
            strony.append(src)
            typer.echo(f"BDL strona {page + 1}/{pages}")
            page += 1
    rows = wskazniki.wartosci_ludnosc(BDL_LATA, teryty, dane, strony)
    out = Path("data/ludnosc.csv")
    b.zapisz(out, rows)
    braki = [r for r in rows if r.status != "ok"]
    typer.echo(f"{out}: {len(rows)} wartości, brak danych: {len(braki)}")
    for r in braki[:10]:
        typer.echo(f"  {r.teryt} {r.rok}: {r.status}")


@app.command("wskazniki")
def wskazniki_cmd(rok: int = 2025, gminy_csv: Path = Path("data/gminy.csv")) -> None:
    """Derived metrics (per capita, percentages) from metrics.yaml for a year."""
    from jg import budzet as b
    from jg import wskazniki
    from jg.metrics import load_metrics

    teryty = _gminy(gminy_csv)
    fakty = []
    for path in (
        Path(f"data/budzet_{rok}.csv"),
        Path(f"data/zobowiazania_{rok}.csv"),
        Path("data/ludnosc.csv"),
    ):
        if not path.is_file():
            raise typer.BadParameter(f"brak {path}")
        fakty.extend(b.wczytaj(path))
    rows = wskazniki.pochodne(load_metrics(Path("metrics.yaml")), rok, teryty, fakty)
    out = Path(f"data/wskazniki_{rok}.csv")
    b.zapisz(out, rows)
    braki = [r for r in rows if r.status != "ok"]
    typer.echo(f"{out}: {len(rows)} wartości, brak danych: {len(braki)}")
    for r in braki[:10]:
        typer.echo(f"  {r.teryt} {r.kod}: {r.status}")


@app.command()
def seria(od: int = 2019, do: int = 2025, gminy_csv: Path = Path("data/gminy.csv")) -> None:
    """Year series: MF file discovery, budget, liabilities, population, metrics, report."""
    import json

    from jg import budzet as budzet_mod
    from jg import potok
    from jg.fetch import HttpClient
    from jg.fetch.reqlog import now_local
    from jg.metrics import load_metrics

    settings = Settings.load()
    teryty = _gminy(gminy_csv)
    if od > do:
        raise typer.BadParameter(f"--od {od} jest większe niż --do {do}")
    lata = list(range(od, do + 1))
    dane = Path("data")
    dane.mkdir(exist_ok=True)
    start = now_local().isoformat(timespec="seconds")
    manifest = settings.archive_dir / "manifest.jsonl"
    przed = set()
    if manifest.is_file():
        przed = {
            json.loads(line)["sha256"] for line in manifest.read_text(encoding="utf-8").splitlines()
        }
    raport = potok.Raport(start, lata)
    with HttpClient(settings, source_id="S1") as client:
        adresy = potok.odkryj(client, lata)
        for rok in lata:
            pliki = {
                kind: client.fetch_to_archive(a.url, note=f"{kind} {rok} IV kw.: {a.title}")
                for kind, a in adresy[rok].items()
            }
            rows = potok.rok_budzetu(rok, teryty, pliki, dane)
            raport.wartosci.extend(rows)
            braki = sum(r.status != "ok" for r in rows)
            typer.echo(f"{rok}: {len(rows)} wartości budżetu i zobowiązań, brak danych: {braki}")
    with HttpClient(settings, source_id="S2") as client:
        lud, _ = potok.ludnosc(client, teryty, lata, settings.gus_bdl_key)
    budzet_mod.zapisz(dane / "ludnosc.csv", lud)
    raport.wartosci.extend(lud)
    typer.echo(f"ludność: {len(lud)} wartości, brak danych: {sum(r.status != 'ok' for r in lud)}")
    poch = potok.pochodne_lat(
        load_metrics(Path("metrics.yaml")), lata, teryty, raport.wartosci, dane
    )
    raport.wartosci.extend(poch)
    potok.zbiorczy(dane / "wartosci.csv", raport.wartosci)
    nowe = potok.nowe_dokumenty(manifest, start, przed)
    bledy = potok.bledy_pobierania(settings.logs_dir / "requests.jsonl", start)
    potok.zapisz_raport(dane / "raport_przebiegu.md", raport, nowe, bledy)
    braki = sum(r.status != "ok" for r in raport.wartosci)
    typer.echo(
        f"data/wartosci.csv: {len(raport.wartosci)} wartości, brak danych: {braki}; "
        f"nowe dokumenty: {len(nowe)}; błędy pobierania: {len(bledy)}"
    )


@app.command("spolki")
def spolki_cmd(lista: Path = Path("spolki.yaml"), rok_mf: int = 2025) -> None:
    """Municipal companies from the list: KRS full extract, gmina shareholder by Rb-NDS REGON."""
    from jg import krs, potok, spolki
    from jg.fetch import HttpClient
    from jg.fetch.archive import ArchivedFile
    from jg.fetch.client import FetchError

    settings = Settings.load()
    with HttpClient(settings, source_id="S1") as client:
        adres = potok.odkryj(client, [rok_mf])[rok_mf]["RbNDS"]
        nds_file = client.fetch_to_archive(adres.url, note=f"RbNDS {rok_mf} IV kw.: {adres.title}")
    nds = nds_file.path
    regony = spolki.regony_gmin(nds)
    potok.sprawdz_okres("Rb-NDS", regony.rok, regony.kwartal, rok_mf)
    zakres, kandydaci = spolki.wczytaj_liste(lista)
    odpisy: dict[str, tuple] = {}
    bledy: dict[str, str] = {}
    with HttpClient(settings, source_id="S5") as client:
        for k in kandydaci:
            try:
                item: ArchivedFile = client.fetch_to_archive(
                    spolki.KRS_URL.format(krs=k.krs), note=f"KRS odpis pełny {k.krs}"
                )
                odpisy[k.krs] = (spolki.odpis_z_archiwum(item), item)
            except FetchError as exc:
                bledy[k.krs] = f"pobranie odpisu KRS: {exc}"
            except (krs.OdpisError, KeyError, ValueError) as exc:
                bledy[k.krs] = f"odczyt odpisu KRS: {exc}"
    wyniki = spolki.weryfikuj(odpisy, kandydaci, regony.gminy, bledy)
    spolki.zapisz(Path("data"), wyniki, zakres)
    for w in sorted(wyniki, key=lambda w: w.kandydat.krs):
        znak = "komunalna" if w.komunalna else "nie"
        nazwa = w.spolka.nazwa[:60] if w.spolka else ""
        typer.echo(f"{w.kandydat.krs} {nazwa}: {znak} ({w.powod})")


@app.command("spolki-finanse")
def spolki_finanse_cmd(od: int = 2019, do: int = 2025) -> None:
    """Company revenue, net result, capex from RDF e-statements (archive/manual/rdf/<KRS>/)."""
    import csv

    from jg import spolki_finanse as sf
    from jg.fetch.archive import Archive

    settings = Settings.load()
    spolki_csv = Path("data/spolki.csv")
    if not spolki_csv.is_file():
        raise typer.BadParameter("brak data/spolki.csv: najpierw `jg spolki`")
    with spolki_csv.open(encoding="utf-8") as fh:
        krs_lista = [r["krs"] for r in csv.DictReader(fh) if r["komunalna"] == "1"]
    katalog = settings.archive_dir / "manual" / "rdf"
    pliki = sf.wczytaj(katalog, Archive(settings.archive_dir)) if katalog.is_dir() else []
    rows = sf.wiersze(pliki, krs_lista, list(range(od, do + 1)))
    sf.zapisz(Path("data/spolki_finanse.csv"), rows)
    ok = sum(r.status == "ok" for r in rows)
    typer.echo(f"data/spolki_finanse.csv: {len(rows)} wartości, ok: {ok}, plików: {len(pliki)}")


@app.command()
def porownania(od: int = 2019, do: int = 2025, gminy_csv: Path = Path("data/gminy.csv")) -> None:
    """Comparison groups and statistics (median, quartiles) for metrics from metrics.yaml."""
    import csv

    from jg import budzet as b
    from jg import porownania as por
    from jg.metrics import load_metrics

    if od > do:
        raise typer.BadParameter(f"--od {od} jest większe niż --do {do}")
    metrics = load_metrics(Path("metrics.yaml"))
    if metrics.grupy is None:
        raise typer.BadParameter("metrics.yaml bez sekcji `grupy`")
    if not gminy_csv.is_file():
        raise typer.BadParameter(f"brak {gminy_csv}: najpierw `jg gminy`")
    with gminy_csv.open(encoding="utf-8") as fh:
        gminy = [por.Gmina(r["teryt"], r["typ"], r["mnpp"] == "1") for r in csv.DictReader(fh)]
    wartosci_csv = Path("data/wartosci.csv")
    if not wartosci_csv.is_file():
        raise typer.BadParameter("brak data/wartosci.csv: najpierw `jg seria`")
    wartosci = b.wczytaj(wartosci_csv)
    lata_danych = {w.rok for w in wartosci}
    brak_lat = [r for r in range(od, do + 1) if r not in lata_danych]
    if brak_lat:
        raise typer.BadParameter(f"data/wartosci.csv bez danych za lata {brak_lat}")
    rows = por.porownaj(
        gminy,
        wartosci,
        metrics.grupy.porownawcze,
        list(range(od, do + 1)),
        metrics.grupy,
    )
    out = Path("data/porownania.csv")
    por.zapisz(out, rows)
    ok = sum(r.status == "ok" for r in rows)
    typer.echo(f"{out}: {len(rows)} wierszy, ok: {ok}, brak danych: {len(rows) - ok}")


def _sprawdz_baze(baza: str) -> None:
    if not (baza.startswith("/") and baza.endswith("/")):
        raise typer.BadParameter("--baza: ścieżka od / zakończona /, np. /jawne-gminy/")


@app.command()
def serwis(
    wyjscie: Path = Path("site/dist"),
    baza: str = typer.Option(
        "/",
        envvar="JG_BAZA",
        help="site root path for the 404 page, e.g. /jawne-gminy/ on GitHub Pages",
    ),
) -> None:
    """Static site from data/*.csv (home page, gmina pages, methodology)."""
    from jg.site import build

    _sprawdz_baze(baza)
    settings = Settings.load()
    try:
        info = build.build(Path("data"), settings.archive_dir, wyjscie, baza=baza)
    except build.BuildError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"{wyjscie}: {info['strony']} stron, {info['gminy']} gmin, rok {info['rok']}")


@app.command()
def przebieg(
    od: int = 2019,
    do: str = typer.Option("auto", help="year or `auto` (last year with complete MF files)"),
    lista_spolek: Path = Path("spolki.yaml"),
    finanse_spolek: bool = typer.Option(
        True,
        "--finanse-spolek/--bez-finansow-spolek",
        help="company finances from manual RDF files; CI runs without them (not in the repo)",
    ),
    baza: str = typer.Option("/", envvar="JG_BAZA", help="site root path (404 page)"),
) -> None:
    """Full pipeline: gmina list, budget series, comparisons, companies, company finances, site."""
    from jg import potok
    from jg.fetch import HttpClient

    _sprawdz_baze(baza)
    if do == "auto":
        try:
            with HttpClient(Settings.load(), source_id="S1") as client:
                rok_do = potok.ostatni_rok(client, od)
        except potok.PotokError as exc:
            raise typer.BadParameter(f"--do auto: {exc}") from exc
        typer.echo(f"ostatni rok z kompletem plików MF: {rok_do}")
    elif do.isdigit():
        rok_do = int(do)
    else:
        raise typer.BadParameter("--do: rok albo `auto`")
    gminy(rok=rok_do)
    seria(od=od, do=rok_do)
    porownania(od=od, do=rok_do)
    spolki_cmd(lista=lista_spolek, rok_mf=rok_do)
    if finanse_spolek:
        spolki_finanse_cmd(od=od, do=rok_do)
    else:
        Path("data/spolki_finanse.csv").unlink(missing_ok=True)
        typer.echo("finanse spółek pominięte (--bez-finansow-spolek)")
    serwis(baza=baza)


if __name__ == "__main__":
    app()
