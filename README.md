# Jawne Gminy

Public financial data for every Polish municipality (*gmina*, 2,479 units) and their municipal companies: budgets, liabilities, population-based indicators and peer-group comparisons. Facts, figures and sources only.

**Website:** https://drkuks.github.io/jawne-gminy/ (in Polish)

## What it does

| Module | Primary source | Coverage |
|---|---|---|
| Municipal budgets: revenue, expenditure, capital expenditure (plan and execution), liabilities, 2019 onwards | Ministry of Finance budget reports Rb-27S, Rb-28S, Rb-NDS, Rb-Z; Statistics Poland BDL (population) | all of Poland |
| Municipal boundaries and map points | GUGiK, State Register of Borders (PRG) | all of Poland |
| Municipal companies: shareholders, governing bodies, revenue, net result, capital expenditure | National Court Register (KRS) API, Financial Statements Repository (RDF) | sample, see `spolki.yaml` |
| Peer-group comparisons | the same indicator definitions for every municipality (`metrics.yaml`) | all of Poland |

Principles:

- Every value carries its source: document, position (e.g. "Rb-28S, group 16xx"), download time and SHA-256 of the archived source file. A missing value is published as `brak danych` ("no data") with a reason, never as 0 or an empty field.
- Totals are cross-checked between reports (Rb-27S/Rb-28S against Rb-NDS, Rb-Z subtotals); a mismatch is published as `brak danych` with the reason.
- Comparisons are mechanical only: plan vs execution, year over year, municipality vs its peer group (type × population band; quartiles). No rankings.
- Only primary sources. Fetching uses official APIs and data packages, an explicit User-Agent, at most one request per 5 s per domain, `ETag`/`If-Modified-Since` caching and a request log. No CAPTCHA solving or access-control circumvention; documents behind such barriers are handled manually.
- Personal data only in connection with a public function (company boards and supervisory boards).

## Usage

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), Node.js (only for accessibility tests).

```sh
uv sync && npm ci                        # dependencies (axe-core is used by browser tests only)
uv run jg przebieg                       # full pipeline: data → data/, website → site/dist/
uv run jg serwis --baza /jawne-gminy/    # rebuild the website only, for a sub-path deployment
uv run jg --help                         # all commands
```

Optional keys go to `.env` (never committed):

```sh
GUS_BDL_KEY=...      # Statistics Poland BDL API key (higher rate limit)
REGON_BIR_KEY=...    # REGON BIR API key
```

Source files are archived under `archive/` (named by SHA-256), request logs and HTTP cache under `var/`, outputs under `data/` and `site/dist/`. None of these are committed.

## Tests

```sh
uv run playwright install chromium
uv run pytest                            # unit tests on real source-file excerpts + browser tests
uv run pytest -m "not browser"           # without the browser
uv run ruff check . && uv run ruff format --check .
```

Browser tests (Playwright, Chromium) cover WCAG 2.1 AA with axe-core, keyboard navigation and 375 px / desktop layouts.

## Automation

- `.github/workflows/tests.yml`: lint, unit and browser tests on every push.
- `.github/workflows/data.yml`: weekly data run, published to GitHub Pages when the repository variable `PUBLIKACJA` is `tak`. Requires the `GUS_BDL_KEY` secret.

## Repository layout

```
jg/            pipeline: fetching, parsers (DBF, XLSX, JSON, XML), indicators, comparisons, CLI
jg/site/       static website generator (Jinja2, SVG charts, CSV export)
site/          templates, styles, design tokens
tests/         tests and fixtures (excerpts of real source files)
sources.yaml   data sources: URLs, formats, terms, limits
metrics.yaml   indicator definitions and peer-group bands
spolki.yaml    municipal companies (KRS numbers)
```

The website, the published data and the pipeline's data messages are in Polish; code, comments and this README are in English. Polish domain terms (*gmina*, *spółka komunalna*, report names such as Rb-27S) are kept as they appear in the sources.

## Licence

Code: [EUPL-1.2](LICENSE). Data: [CC BY 4.0](LICENSE-DATA.md), with attribution to the primary sources.

Sister project: [Oko na Oławę](https://github.com/drkuks/okiemnaolawe), a detailed site for a single municipality.
