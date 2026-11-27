# GeoHealth ETL

An **environmental burden and equity explorer** for the 357 census tracts of Middlesex County, Massachusetts. It maps conditions linked to mental health in research (food environment, green space, traffic and noise, air quality, access to services) and shows **who lives with them**: people of color, immigrants, limited-English households and people in poverty. A **Find priority tracts** panel lists the top tracts for common planning uses, such as tree planting, new parks or traffic mitigation.

The tool has three parts:

1. **ETL pipeline:** pulls about a dozen public datasets, puts them all on 2020 census tracts, ranks every factor within the county, combines them into theme scores and an overall burden score, and loads one spatial table into PostGIS.
2. **Spatial modeling:** spatial autocorrelation (Moran's I / LISA hot spots) and K-means clustering of tracts into four food/mental-health profiles.
3. **Web explorer:** a FastAPI + MapLibre map with three views (one factor, burden score, clusters), a button to show any population group on the map alongside the current factor, a "who's affected" comparison, a profile for each tract, and a **Find priority tracts** panel with top-10/25/50 lists for typical use cases. An optional LLM agent endpoint (`POST /api/query`, OpenAI `gpt-4o-mini` via LangChain) is still available to API users, but isn't on the page.

> **What this tool does and doesn't show.** It measures *where conditions are worse and who lives there*. It does **not** show that these conditions cause mental illness in Middlesex County. The mental-health layers come from CDC PLACES, which CDC estimates mostly from tract demographics (age, sex, race, education), not from local measurements. So they can't be used to test environmental factors. See [Known limitations](#known-limitations).

Acronyms and technical terms (mRFEI, LISA, NHANES, ACS…) are defined in the [Glossary](#glossary) at the end.

## Quick start

Requirements: [uv](https://docs.astral.sh/uv/), Docker, Python 3.12 (uv installs it if needed).

```bash
# 1. Start PostGIS
docker compose up -d

# 2. Install dependencies
uv sync

# 3. Create .env in the project root with your API keys (see "Configuration")

# 4. Build the table (includes clustering)
uv run geohealth etl

# 5. Open the explorer at http://localhost:8000
uv run geohealth serve
```

## Configuration

Settings are read from a `.env` file in the project root. It's listed in `.gitignore`, so don't commit it.

| Variable | Required for | Notes |
|---|---|---|
| `CENSUS_API_KEY` | `etl` | Free; request one at <https://api.census.gov/data/key_signup.html> and click the activation link in the email. |
| `OPENAI_API_KEY` | optional | Only for the `POST /api/query` AI endpoint. The map page doesn't use it. |
| `DATABASE_URL` | optional | Defaults to `postgresql+psycopg2://geo_user:geo_password@localhost:5432/geohealth` (matches `docker-compose.yml`). Keep the `+psycopg2` driver prefix. |

```dotenv
CENSUS_API_KEY=your_census_key
OPENAI_API_KEY=sk-...
```

The study area (Middlesex County, MA) and coordinate systems are set in [src/geohealth_etl/config.py](src/geohealth_etl/config.py).

## Commands

| Command | What it does | Time |
|---|---|---|
| `uv run geohealth etl` | Downloads all sources, builds `tract_food_mental_health` including factor scores and cluster columns, writes it to PostGIS | First run: about 10–20 minutes (OpenStreetMap queries and a 153 MB EJScreen download). Later runs reuse `data_cache/` and `cache/`. |
| `uv run geohealth cluster` | Re-runs only the clustering on the existing table, without downloading again (e.g. after changing [cluster.py](src/geohealth_etl/modeling/cluster.py)) | Under a minute |
| `uv run geohealth export` | Writes a static copy of the map to `site/` (page + data files) that any static web host can serve. Options: `--out DIR`, `--simplify METRES` (default 10; 0 = full detail), `--single-file` (one self-contained HTML file instead; see below) | A few seconds |
| `uv run geohealth serve` | Starts the web explorer at <http://localhost:8000> (auto-reloads on code changes) | Runs until stopped |

## Data sources

| # | Source | Used for | Tract vintage |
|---|---|---|---|
| 1 | [US Census TIGER/Line 2023](https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.html) | Tract boundaries | 2020 |
| 2 | [CDC PLACES](https://data.cdc.gov/resource/cwsq-ngmh) | Frequent mental distress %, depression % | 2020 |
| 3 | [USDA Food Access Research Atlas 2019](https://www.ers.usda.gov/data-products/food-access-research-atlas/) | Food-desert flag, low-income flag, poverty rate, median family income | **2010** |
| 4 | [OpenStreetMap](https://www.openstreetmap.org/) via OSMnx | Supermarkets, greengrocers, markets, fast food, convenience stores, plus liquor stores, bars and pubs (one query) | n/a |
| 5 | [NHANES 2017–Mar 2020](https://wwwn.cdc.gov/nchs/nhanes/) + [ACS 2022 5-year](https://www.census.gov/data/developers/data-sets/acs-5year.html) | Model-estimated vegetable share of veg + meat intake | 2020 |
| 6 | [Census ACS 2022 5-year](https://www.census.gov/data/developers/data-sets/acs-5year.html) (tables B03002, B05002, C16002, B17001) | % people of color, % foreign-born, % limited-English households, % below poverty | 2020 |
| 7 | [CDC/ATSDR Social Vulnerability Index 2022](https://www.atsdr.cdc.gov/place-health/php/svi/) | Overall social vulnerability ranking | 2020 |
| 8 | [EPA EJScreen 2024](https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/RLR5AX), via the Public Environmental Data Partners copy on Harvard Dataverse (EPA took EJScreen offline in 2025) | PM2.5, NO₂, diesel particulate, traffic proximity | 2020 |
| 9 | [NLCD Tree Canopy 2021](https://www.mrlc.gov/data/nlcd-2021-usfs-tree-canopy-cover-conus) (USFS/MRLC), 30 m raster | Tree canopy % | n/a (raster) |
| 10 | [MassGIS](https://www.mass.gov/orgs/massgis-bureau-of-geographic-information) (the state GIS office), downloaded from its public data bucket | Protected & Recreational Open Space (public access only); MassDOT Road Inventory with traffic counts (AADT); active rail lines; MBTA bus stops and rapid-transit and commuter-rail stations; DPH community health centres and hospitals; libraries; places of worship; **2020 Environmental Justice populations** | 2020 block groups for EJ |
| 11 | [Census 2020↔2010 tract relationship file](https://www.census.gov/geographies/reference-files/time-series/geo/relationship-files.html) | Moving the 2010-tract USDA data onto 2020 tracts | both |

Each factor's label, unit, direction, source and type (measured, proxy or modelled) is defined in one place: the factor catalogue, [factors.py](src/geohealth_etl/factors.py).

**Caches:** OSMnx keeps OpenStreetMap responses in `./cache/`. The other large downloads (MassGIS shapefiles in `data_cache/massgis/`, about 200 MB; the county subset of EJScreen; the tree canopy raster; SVI; the tract relationship file) are kept in `./data_cache/`.

MassGIS files come straight from its public S3 bucket (`download.massgis.digital.mass.gov`), because mass.gov's own pages block automated requests. Delete a folder to force fresh data. Both folders are git-ignored.

## How it works

### ETL ([etl/pipeline.py](src/geohealth_etl/etl/pipeline.py))

1. Fetch tract polygons, CDC, USDA and OSM food data ([etl/source.py](src/geohealth_etl/etl/source.py)). The USDA data uses 2010 tracts, so each 2020 tract gets the land-area-weighted average of the 2010 tracts it overlaps. Food-desert and low-income flags are set when at least half the land had them.
2. Estimate diet per tract ([modeling/diet_estimator.py](src/geohealth_etl/modeling/diet_estimator.py)): a gradient-boosting model is trained on individual NHANES dietary recalls (age, sex, education, race/ethnicity, income-to-poverty ratio → vegetable share of veg + meat grams), then applied to each tract's ACS demographic profile.
3. Fetch the contributing-factor layers ([etl/factor_sources.py](src/geohealth_etl/etl/factor_sources.py)):
   - census demographics, SVI and EJScreen, joined on `geoid`
   - tree canopy, averaged over each tract's 30 m raster cells
   - from MassGIS: the share of each tract that is publicly accessible open space, and the distance from its centre to the nearest one; daily vehicle-km of traffic per km², from MassDOT traffic counts × road length; the share of the tract within 300 m of an active railway; the distance to the nearest community health centre or hospital; counts of MBTA stops and stations, and of libraries and places of worship, within 1 km of the tract's centre; and the share of residents living in state-designated Environmental Justice block groups
   - alcohol outlets within 1 km, taken from the same OpenStreetMap query as the food outlets
4. Compute tract area in a projected CRS (EPSG:26986, MA Mainland).
5. Join everything on the 11-digit `geoid`, and count food outlets per tract with a point-in-polygon join.
6. Derive densities and the **mRFEI** (modified Retail Food Environment Index = healthy outlets ÷ all outlets × 100; higher is healthier).
7. Score the factors (below).
8. Run the spatial clustering (below).
9. Write to PostGIS (EPSG:4326) with GIST and `geoid` indexes ([db.py](src/geohealth_etl/db.py)).

### Factor scores ([modeling/scores.py](src/geohealth_etl/modeling/scores.py))

This is the same approach EPA EJScreen and the CDC Environmental Justice Index use:

1. **Percentiles:** each factor is ranked 0–100 within the county (`<factor>_pctile`). For exposures, the scale is flipped where needed so **100 always means the worst condition**. For example, the tract with the least tree canopy gets 100. For population and modelled-health columns, 100 = highest share or estimate.
2. **Theme scores:** each theme score (`theme_<theme>_score`) is the average of its exposure percentiles. There are five themes: food & alcohol retail, green space & nature, traffic & noise, air quality, and services & community.
3. **Burden score:** `burden_score` is the average of the five theme scores, so each theme counts equally however many factors it has. `burden_pctile` ranks it 0–100. In the map's Burden view you can untick themes, and the page recalculates the ranking in the browser.

### Clustering ([modeling/cluster.py](src/geohealth_etl/modeling/cluster.py))

Runs as the last step of `etl` (via `add_spatial_clusters`), or on its own with `geohealth cluster`. Uses tracts with complete data on mental distress, mRFEI, less-healthy outlet density and median family income:

- **Global / Local Moran's I** on frequent mental distress (queen contiguity, 999 permutations) → `lisa_cluster` (High-High hot spot, Low-Low cold spot, outliers, or Not Significant at p < 0.05).
- **K-means (k = 4)** on standardized features, labelled by mean distress: *Low Distress / Higher Income*, *Moderate Distress / Mixed Food Access*, *Elevated Distress / Dense Retail*, *High Distress / Low Income & Food Swamp*. A *food swamp* is an area where most food outlets are fast food or convenience stores (low mRFEI). That differs from a *food desert* (USDA `lila_1and10_flag`), where healthy food is far away; only about 10% of the high-distress cluster's tracts are food deserts.

Tracts missing any input get `Insufficient Data` and appear gray on the map. Excluding them can leave a tract with no neighbors, which libpysal reports as `WARNING: ... is an island`; that tract still gets a K-means cluster but can't be a LISA hot or cold spot.

### Map views ([api/static/index.html](src/geohealth_etl/api/static/index.html))

| View | What it shows |
|---|---|
| **Factor** | One factor, theme score or the burden score, coloured by county percentile. Exposures use a yellow→red scale (red = worse); population and mental-health layers use purple (darker = higher share). The note under the menu gives the factor's definition, source and type. Mental-health layers come with a warning that they're modelled estimates. |
| **Burden** | The overall burden score. Theme checkboxes let you choose which themes count. |
| **Clusters** | The original K-means food and mental-distress clusters. |

- **Who's affected:** this box compares the average of the current exposure in the quarter of tracts with the *most* of a chosen group (for example people of color) against the quarter with the *fewest*, e.g. *"In the 88 tracts with the most people of color (42.4% or more of residents): 30.7% tree canopy; in the 88 with the fewest (19.8% or less): 57.2%"*. These are plain averages across tracts, not weighted by population. The **Show [group] on the map** button switches the map to a two-variable (bivariate) view of the current factor or burden score *and* the chosen group, each split into thirds, giving a 3×3 colour grid. The darkest tracts are in the worst third for conditions **and** the highest third for the group. The grid, with tract counts, appears in the box. In the Clusters view, where colours are categories that can't be blended, the button instead outlines the group's tracts: purple for the quarter with the most of the group, teal for the quarter with the fewest.
- **Correlation chart:** a scatter plot under the comparison, with one dot per tract (group share across, factor value up). The two compared quarters are coloured purple and teal, and a line shows the average for each fifth of tracts. Very skewed factors such as traffic switch to a log scale automatically. A sentence gives the Spearman rank correlation (ρ) in plain words, labelled weak (0.1–0.3), moderate (0.3–0.5) or strong (0.5 and above). Clicking a dot opens that tract.
- **Tract profile:** clicking any tract, or any row in the results table, opens a profile with each theme score, every factor's value and percentile (tagged *measured*, *proxy* or *modelled*), who lives there, and the modelled mental-health estimates.

### Find priority tracts ([api/static/index.html](src/geohealth_etl/api/static/index.html))

A sidebar panel with preset **use cases**. Choose one, pick **top 10, 25 or 50**, and the matching tracts are filled yellow on the map, the map zooms to them, and they're listed in the **results table** under the map. Click a row to zoom to that tract and open its profile. Everything is calculated in the browser from the tract data, with no AI and no extra server calls.

| Use case | Ranked by the average county percentile of (100 = worst) |
|---|---|
| 🌳 Tree planting & cooling | tree canopy |
| 🏞️ New park or open space | distance to public open space, public open space % |
| 🚦 Traffic noise & air mitigation | traffic volume density, traffic proximity, near an active railway, NO₂, diesel PM |
| 🥦 Healthy food access | healthy food retail mix (mRFEI), fast food & convenience density |
| 🍺 Alcohol licensing review | alcohol outlets nearby |
| 🏥 Health or mental-health service siting | distance to health centre or hospital, transit stops nearby |
| 📚 Community & social connection | libraries & places of worship nearby, transit stops nearby |
| 📊 Overall burden hot spots | overall burden (themes ticked in the Burden tab) |

With **"and prioritise tracts with more [group]"** ticked (the default), the conditions score is averaged 50/50 with the county percentile of the group chosen under *Who's affected*. That puts high-need, high-burden tracts first. Untick it to rank on conditions alone. Tracts with no residents are left out. The table shows the priority score, the conditions-only score, the raw values behind them and the group share.

The use cases are defined in the `USE_CASES` list in the page script. Adding one means one entry naming its factors.

### AI query endpoint (API only) ([agent/spatial_agent.py](src/geohealth_etl/agent/spatial_agent.py))

The AI box has been removed from the page, but `POST /api/query` still works for API users: the question and the table schema go to `gpt-4o-mini`, which returns an explanation and a read-only SQL query (checked to be `SELECT`/`WITH` only), and matching tracts come back as GeoJSON.

The agent's instructions in [agent/prompt.py](src/geohealth_etl/agent/prompt.py) are generated from the factor catalogue, so every new factor (with its percentile and theme columns) can be queried automatically. It knows that "worst 20% for X" means `X_pctile >= 80`.

The agent returns at most **50 tracts** unless the question asks for all of them.

## Output table: `tract_food_mental_health`

One row per 2020 census tract in Middlesex County (357 tracts).

| Column | Description |
|---|---|
| `geoid`, `tract_name` | 11-digit tract FIPS code and name |
| `area_sqkm` | Tract area (km²) |
| `frequent_mental_distress_pct`, `depression_pct`, `loneliness_pct` | CDC PLACES modelled adult prevalence (%) |
| `lila_1and10_flag`, `low_income_flag` | USDA low-income & low-access (food desert) flag; low-income flag |
| `poverty_rate`, `median_family_income` | USDA atlas socioeconomics (mapped from 2010 to 2020 tracts) |
| `healthy_outlet`, `less_healthy_outlet` | OSM outlet counts |
| `healthy_per_sqkm`, `less_healthy_per_sqkm` | Outlet densities |
| `mrfei_score` | 0–100; 0 when a tract has no outlets |
| `est_veg_meat_share_pct`, `est_veg_meat_zscore` | Model-estimated vegetable share and its county z-score |
| Factor columns | One column per factor in [factors.py](src/geohealth_etl/factors.py), e.g. `tree_canopy_pct`, `pm25`, `traffic_proximity`, `pct_foreign_born` |
| `<factor>_pctile` | County percentile of each factor (exposures: 100 = worst) |
| `theme_<theme>_score` | Theme scores 0–100 (100 = worst): `food`, `green`, `noise`, `air`, `services` |
| `burden_score`, `burden_pctile` | Average of theme scores, and its county percentile |
| `cluster_id`, `cluster_label` | K-means profile (`-1` / `Insufficient Data` if excluded) |
| `lisa_cluster`, `lisa_pvalue` | Local Moran's I classification and pseudo p-value |
| `geometry` | MultiPolygon, SRID 4326 |

## Publishing as a static website

Once built, the map doesn't need a server or database: everything (colours, burden recalculation, "Who's affected", priority rankings) runs in the browser from a few data files. `geohealth export` writes them:

```
site/
├── index.html            # the same page the local server uses
└── data/
    ├── tracts.geojson    # 357 tracts: simplified outlines + every factor value, percentile and score
    ├── factors.json      # factor catalogue (labels, themes, units, sources)
    └── meta.json         # "data as of" date and source list (shown under the page title)
```

- **Size:** about 0.25 MB compressed in total (tracts 1.3 MB raw → 0.22 MB as served), down from 0.62 MB for the full-detail outlines.
- **Simplification:** outlines are simplified with `shapely.coverage_simplify`, which simplifies all tracts *together*, so neighbouring tracts still share identical borders (no gaps or overlaps). At the default 10 m tolerance, 38% of the corner points remain and the median tract outline changes by about 0.1% of its area.
- **Preview:** `python3 -m http.server 8080 --directory site`, then open <http://localhost:8080>. Visitors to a *hosted* site don't need this; the host is the web server. It's only needed locally, because browsers block a page opened straight from disk (`file://`) from loading the separate `data/` files.
- **Single file (`--single-file`):** writes `site/geohealth-map.html`, one file (~1.4 MB, ~0.25 MB zipped) with the data embedded. It opens by **double-click**, as an **email attachment** or from a **shared drive**, with no server. It still needs internet for the map library and basemap tiles, and copies don't update themselves, so re-export and re-send after `geohealth etl`.
- **Publish:** upload the `site/` folder to any static host, e.g. GitHub Pages (commit `site/` or publish it from a `gh-pages` branch), Netlify or Cloudflare Pages (drag and drop), or a city or organisation web server.
- **Update:** `geohealth etl`, then `geohealth export`, then re-upload.
- **One page for every setup:** the page first looks for data embedded in the file (single-file export), and otherwise reads `data/factors.json`, `data/tracts.geojson` and `data/meta.json` from relative paths. `geohealth serve` serves those paths from PostGIS (full detail), and the folder export writes them as files, so there's only one `index.html`.
- **What still comes from the internet:** the MapLibre library (unpkg.com) and the CARTO Positron basemap tiles. CARTO's free basemaps are fine for modest traffic and require the attribution the map already shows.

## API

| Endpoint | Description |
|---|---|
| `GET /` | Map explorer |
| `GET /data/factors.json`, `/data/tracts.geojson`, `/data/meta.json` | What the page reads (same files as the static export) |
| `GET /api/factors` | The factor catalogue: themes, and each factor's label, unit, direction, source and type |
| `GET /api/tracts` | All tracts with factor, percentile, score and cluster fields as a GeoJSON FeatureCollection. Columns missing from an older table are skipped. |
| `POST /api/query` | Body `{"question": "..."}` → `{explanation, sql, match_count, geojson}` |

## Known limitations

- **Mental-health layers can't show causes.** CDC PLACES tract estimates come from a model that uses age, sex, race/ethnicity and education plus county-level effects. In a test on this county, those demographics alone explained about 78% of the tract-to-tract variation in "frequent mental distress" (67% for depression). Environmental factors never enter CDC's model, so any correlation between, say, tree canopy and PLACES distress here runs through demographics. That's why mental health is shown as context, not as an outcome to explain. A real test of contributing factors needs measured outcomes, such as Massachusetts DPH emergency-visit data by town.
- **Associations, not causes:** even the measured equity comparisons ("tracts with more immigrants have less tree canopy") describe differences between places, not why they exist, and don't apply to every individual in a tract (the ecological fallacy).
- **Race and nativity are markers, not risk factors:** they are included to show *who* carries environmental burdens, which in research reflects structural factors such as segregation and discrimination, not anything about the people themselves.
- **Proxies and simplifications:**
  - Noise is approximated by traffic (EJScreen traffic proximity and MassDOT traffic volume) and closeness to active railways, because the US Department of Transportation noise map couldn't be downloaded automatically. Aircraft noise (Hanscom Field, Logan approaches) isn't covered.
  - MassDOT traffic counts are mostly from 2016, and many segments have estimated rather than counted values.
  - Health access counts only community health centres and hospitals. MassGIS has no layer for private therapists or other outpatient mental-health providers.
  - "Within 1 km" counts and distances are measured from each tract's centre, which is less meaningful for large suburban tracts.
  - Distances are straight-line, not walking routes.
  - OpenStreetMap coverage varies by town.
- **Percentiles are county-relative:** "worst" means worst *within Middlesex County*, not against state or national standards. The Who's affected averages aren't weighted by population.
- **Data years differ:** EJScreen 2024 (frozen, since EPA discontinued it), tree canopy 2021, ACS 2018–2022, USDA atlas 2019, OpenStreetMap as of each run.
- **Diet estimates are rough:** the NHANES model's individual-level cross-validated error is about 29 percentage points. Tract estimates reflect demographic composition only, not local food access.
- **mRFEI = 0 is ambiguous:** it can mean no healthy outlets, or no outlets at all. The burden scoring leaves tracts with no food outlets unranked on mRFEI, so they aren't counted as food swamps. When you query `mrfei_score` directly, check `healthy_outlet + less_healthy_outlet` first.
- **Priority lists are a starting point:** they rank tracts on the measures listed above with equal weights. Check the tract profile and talk to residents before committing resources.
- **The AI endpoint (API only) can be wrong:** review the generated SQL it returns before relying on a result.
- **Not reproducible over time:** each `etl` run downloads current OpenStreetMap data, and the diet model retrains on every run.

## Troubleshooting

| Error | Fix |
|---|---|
| `Connection refused` on port 5432 | The database container stopped: `docker compose up -d` |
| `No module named 'psycopg'` | `DATABASE_URL` is missing the driver prefix; use `postgresql+psycopg2://...` |
| `CENSUS_API_KEY is not set` | Add the key to `.env`, and make sure you clicked the activation email |
| `OpenAIError: Missing credentials` (from `/api/query`) | Add `OPENAI_API_KEY` to `.env` |
| `/api/tracts` errors with a missing `cluster_*` column | The table was built by an older version without clustering; run `uv run geohealth cluster` (or `etl`) |
| Map menus missing new factors | The table was built before the factor update; run `uv run geohealth etl` |
| ETL pauses at step 4 ("Fetching OpenStreetMap food and alcohol...") | The public Overpass server's status check currently fails, so OSMnx waits 60 seconds between attempts. It's only slow the first time; the result is cached in `cache/` |

## Project layout

```
src/geohealth_etl/
├── cli.py              # `geohealth` command: etl | cluster | serve
├── config.py           # Study area, CRS, env vars (.env loaded here), cache folder
├── factors.py          # Factor catalogue: every measure's label, theme, unit, direction, source
├── db.py               # PostGIS read/write + indexes
├── tract_data.py       # Builds the page's data (tract GeoJSON, catalogue, meta) for serve and export
├── export.py           # `geohealth export`: static site in site/
├── etl/
│   ├── pipeline.py     # ETL orchestration
│   ├── source.py       # TIGER, CDC PLACES, USDA, OSM food fetchers
│   └── factor_sources.py  # ACS, SVI, EJScreen, tree canopy, MassGIS layers, 2010→2020 crosswalk
├── modeling/
│   ├── diet_estimator.py  # NHANES → ACS small-area diet model
│   ├── scores.py          # Percentiles, theme scores, burden score
│   └── cluster.py         # Moran's I, LISA, K-means
├── agent/
│   ├── prompt.py       # LLM system prompt, generated from the factor catalogue
│   └── spatial_agent.py
└── api/
    ├── app.py          # FastAPI routes
    └── static/index.html  # MapLibre front end
```

## Glossary

### Data sources and organizations

| Term | Meaning |
|---|---|
| **ACS** | American Community Survey: the Census Bureau's ongoing survey of demographics and income. This project uses the 2022 5-year estimates, which pool 2018–2022 responses so that small areas like tracts have enough data. |
| **CDC** | Centers for Disease Control and Prevention. |
| **CDC PLACES** | *Population Level Analysis and Community Estimates*: CDC's modelled estimates of health measures (here, frequent mental distress and depression) for small areas such as census tracts. They are model-based estimates, not direct survey counts. |
| **EJScreen** | EPA's Environmental Justice Screening tool: national tract and block-group data on pollution and demographics. EPA took it offline in 2025, and this project uses the 2024 version preserved by the Public Environmental Data Partners. |
| **ERS** | Economic Research Service, the USDA agency that publishes the Food Access Research Atlas. |
| **NHANES** | National Health and Nutrition Examination Survey: a CDC survey that combines interviews, physical exams and detailed 24-hour dietary recalls. It's used here to train the diet model. |
| **NLCD** | National Land Cover Database (MRLC consortium). Its *Tree Canopy* product estimates the percentage of each 30 m square covered by trees. |
| **OSM** | OpenStreetMap: a crowd-sourced world map, used here for food outlet locations. **OSMnx** is the Python library that downloads OSM data. |
| **SVI** | CDC/ATSDR Social Vulnerability Index: ranks tracts within a state on 16 census variables (poverty, housing, language, disability and others), from 0 (least vulnerable) to 1 (most). |
| **TIGER/Line** | *Topologically Integrated Geographic Encoding and Referencing*: the Census Bureau's official boundary files, used here for census tract shapes. |
| **USDA** | United States Department of Agriculture. |

### Food environment and health measures

| Term | Meaning |
|---|---|
| **mRFEI** | *Modified Retail Food Environment Index*, a measure developed by the CDC: healthy food outlets ÷ (healthy + less-healthy outlets) × 100. Ranges from 0 to 100, and higher means a healthier mix. Here "healthy" is supermarkets, greengrocers and marketplaces, and "less healthy" is fast food and convenience stores, all from OSM. This is an approximation of the CDC definition, which uses commercial business listings. A score of 0 can also mean a tract has no outlets at all. |
| **LILA** | *Low Income and Low Access*, USDA's food-desert definition. `lila_1and10_flag` = 1 means a low-income tract where many residents live more than 1 mile (urban) or 10 miles (rural) from a supermarket. |
| **Food desert** | An area where healthy food is hard to reach. This project uses the USDA LILA flag as its food-desert measure. |
| **Food swamp** | An area where most food outlets are fast food or convenience stores (low mRFEI), even if supermarkets exist nearby. |
| **PM2.5** | Fine particulate matter: airborne particles 2.5 µm or smaller, measured in µg/m³. Linked to heart, lung and, in some studies, mental health. |
| **NO₂** | Nitrogen dioxide, mostly from vehicle exhaust, in parts per billion (ppbv). |
| **Diesel PM** | Particulate matter from diesel engines (trucks, buses, rail, construction). |
| **Traffic proximity** | EJScreen's count of daily vehicles on major roads within 500 m, divided by distance. Used here as a stand-in for road noise. |
| **Limited-English household** | A Census term: a household where no one aged 14 or older speaks English "very well". |
| **Frequent mental distress** | CDC PLACES measure: % of adults reporting 14 or more days of poor mental health in the past 30 days. |

### Spatial statistics and modeling

| Term | Meaning |
|---|---|
| **Percentile** | A tract's rank within the county, 0–100. For exposures in this project, 100 = worst. |
| **EJ population (Massachusetts)** | A 2020 census block group the state designates as an Environmental Justice population because of its minority share, income, and/or the share of households with limited English. |
| **AADT** | Annual Average Daily Traffic: the average number of vehicles using a road segment per day. |
| **Theme score / burden score** | The average of percentiles within a theme, and the average of the theme scores (100 = most burdened). |
| **Bivariate map** | A map that colours each area by two variables at once, using a grid of colours (here 3×3). |
| **Measured / proxy / modelled** | How a factor was obtained: directly counted or observed; a stand-in for something not measured directly; or a statistical estimate. |
| **Spearman correlation (ρ)** | How consistently one measure rises or falls as another rises, based on rank order. It runs from −1 (one always falls as the other rises) through 0 (no pattern) to +1 (both always rise together). It isn't thrown off by extreme values. |
| **Ecological fallacy** | The mistake of assuming that a pattern across areas applies to each individual living in them. |
| **Moran's I** | A measure of spatial autocorrelation: whether similar values cluster together on the map (positive), are scattered randomly (around 0), or alternate (negative). |
| **LISA** | *Local Indicators of Spatial Association* (here, Local Moran's I): flags individual tracts as **hot spots** (high values surrounded by high values), **cold spots** (low surrounded by low), or **outliers** (high surrounded by low, or the reverse). |
| **Queen contiguity** | The neighbor rule used for Moran's I: two tracts are neighbors if they share any boundary point, even a single corner (like a chess queen's moves). |
| **Island** | A tract with no neighbors in the analysis, for example because all its neighbors were excluded for missing data. |
| **K-means** | A clustering algorithm that groups tracts into *k* groups (here 4) with similar values across the chosen measures. |
| **Small-area estimation** | Estimating a measure for small areas (tracts) where it isn't measured directly. Here, a model trained on individuals in NHANES is applied to each tract's ACS demographics. |
| **MAE** | Mean Absolute Error: the average size of the model's prediction errors, in percentage points. |

### Geography and data

| Term | Meaning |
|---|---|
| **Census tract** | A small Census Bureau area of roughly 1,200–8,000 people, redrawn every 10 years (2010 and 2020 tracts differ). |
| **FIPS / GEOID** | *Federal Information Processing Standards* codes identifying places. A tract's 11-digit `geoid` is state (2 digits) + county (3) + tract (6), e.g. `25017369000` = Massachusetts (25), Middlesex County (017), tract 3690.00. |
| **CRS** | Coordinate Reference System: how coordinates map to locations on Earth. |
| **EPSG** | A registry of CRS codes. `EPSG:4326` is plain latitude/longitude (WGS 84), used for storage and web maps. `EPSG:26986` is the Massachusetts Mainland projection in meters, used for accurate area calculations. |
| **SRID** | Spatial Reference System Identifier: the CRS code attached to a geometry column in PostGIS (here 4326). |
| **MultiPolygon** | A geometry made of one or more polygons, so a tract split into separate pieces is still one shape. |
| **GeoJSON** | A JSON format for geographic shapes and their attributes. It's what the API sends to the map. |

### Software and infrastructure

| Term | Meaning |
|---|---|
| **ETL** | *Extract, Transform, Load*: download data, clean and combine it, and save it to a database. |
| **PostGIS** | A PostgreSQL extension that adds geographic data types and spatial functions (`ST_AsGeoJSON`, spatial joins and so on). |
| **GIST index** | *Generalized Search Tree*: the PostgreSQL index type that makes spatial queries fast. |
| **SQL** | Structured Query Language, used to query the database. |
| **API** | Application Programming Interface. Here, the web endpoints the map page calls (`/api/tracts`, `/api/query`). |
| **LLM** | Large Language Model. Here, OpenAI's `gpt-4o-mini`, which turns questions into SQL. |
| **FastAPI / Uvicorn** | The Python web framework and server that run the explorer. |
| **MapLibre** | The open-source JavaScript library that draws the interactive map. |
