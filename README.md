# GeoHealth ETL

A census-tract-level map of **food environment and mental health** for Middlesex County, Massachusetts, with an AI assistant that turns plain-English questions into PostGIS queries and highlights the matching tracts.

The tool has three parts:

1. **ETL pipeline:** pulls five public datasets, joins them to 2020 census tracts, engineers food-environment metrics, and loads one spatial table into PostGIS.
2. **Spatial modeling:** spatial autocorrelation (Moran's I / LISA hot spots) and K-means clustering of tracts into four food/mental-health profiles.
3. **Web explorer:** a FastAPI + MapLibre map of the clusters, plus an LLM agent (OpenAI `gpt-4o-mini` via LangChain) that answers questions such as *"Find food-desert tracts where frequent mental distress is above 14%"*.

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
| `OPENAI_API_KEY` | AI queries in `serve` | The map works without it; only **Run AI Query** needs it. |
| `DATABASE_URL` | optional | Defaults to `postgresql+psycopg2://geo_user:geo_password@localhost:5432/geohealth` (matches `docker-compose.yml`). Keep the `+psycopg2` driver prefix. |

```dotenv
CENSUS_API_KEY=your_census_key
OPENAI_API_KEY=sk-...
```

The study area (Middlesex County, MA) and coordinate systems are set in [src/geohealth_etl/config.py](src/geohealth_etl/config.py).

## Commands

| Command | What it does | Time |
|---|---|---|
| `uv run geohealth etl` | Downloads all sources, builds `tract_food_mental_health` including the cluster columns, writes it to PostGIS | A few minutes; downloads ~200 MB each run |
| `uv run geohealth cluster` | Re-runs only the clustering on the existing table, without downloading again (e.g. after changing [cluster.py](src/geohealth_etl/modeling/cluster.py)) | Under a minute |
| `uv run geohealth serve` | Starts the web explorer at <http://localhost:8000> (auto-reloads on code changes) | Runs until stopped |

## Data sources

| # | Source | Used for | Tract vintage |
|---|---|---|---|
| 1 | [US Census TIGER/Line 2023](https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.html) | Tract boundaries | 2020 |
| 2 | [CDC PLACES](https://data.cdc.gov/resource/cwsq-ngmh) | Frequent mental distress %, depression % | 2020 |
| 3 | [USDA Food Access Research Atlas 2019](https://www.ers.usda.gov/data-products/food-access-research-atlas/) | Food-desert flag, low-income flag, poverty rate, median family income | **2010** |
| 4 | [OpenStreetMap](https://www.openstreetmap.org/) via OSMnx | Supermarkets, greengrocers, markets, fast food, convenience stores | n/a |
| 5 | [NHANES 2017–Mar 2020](https://wwwn.cdc.gov/nchs/nhanes/) + [ACS 2022 5-year](https://www.census.gov/data/developers/data-sets/acs-5year.html) | Model-estimated vegetable share of veg + meat intake | 2020 |

OSMnx caches API responses in `./cache/`. Delete that folder to force fresh OpenStreetMap data.

## How it works

### ETL ([etl/pipeline.py](src/geohealth_etl/etl/pipeline.py))

1. Fetch tract polygons, CDC, USDA and OSM data ([etl/source.py](src/geohealth_etl/etl/source.py)).
2. Estimate diet per tract ([modeling/diet_estimator.py](src/geohealth_etl/modeling/diet_estimator.py)): a gradient-boosting model is trained on individual NHANES dietary recalls (age, sex, education, race/ethnicity, income-to-poverty ratio → vegetable share of veg + meat grams), then applied to each tract's ACS demographic profile.
3. Compute tract area in a projected CRS (EPSG:26986, MA Mainland).
4. Join everything on the 11-digit `geoid`, and count food outlets per tract with a point-in-polygon join.
5. Derive densities and the **mRFEI** (modified Retail Food Environment Index = healthy outlets ÷ all outlets × 100; higher is healthier).
6. Run the spatial clustering below.
7. Write to PostGIS (EPSG:4326) with GIST and `geoid` indexes ([db.py](src/geohealth_etl/db.py)).

### Clustering ([modeling/cluster.py](src/geohealth_etl/modeling/cluster.py))

Runs as the last step of `etl` (via `add_spatial_clusters`), or on its own with `geohealth cluster`. Uses tracts with complete data on mental distress, mRFEI, less-healthy outlet density and median family income:

- **Global / Local Moran's I** on frequent mental distress (queen contiguity, 999 permutations) → `lisa_cluster` (High-High hot spot, Low-Low cold spot, outliers, or Not Significant at p < 0.05).
- **K-means (k = 4)** on standardized features, labelled by mean distress: *Low Distress / Higher Income*, *Moderate Distress / Mixed Food Access*, *Elevated Distress / Dense Retail*, *High Distress / Low Income & Food Swamp*. A *food swamp* is an area where most food outlets are fast food or convenience stores (low mRFEI). That differs from a *food desert* (USDA `lila_1and10_flag`), where healthy food is far away; only about 7% of the high-distress cluster's tracts are food deserts.

Tracts missing any input get `Insufficient Data` and appear gray on the map. Excluding them can leave a tract with no neighbors, which libpysal reports as `WARNING: ... is an island`; that tract still gets a K-means cluster but can't be a LISA hot or cold spot.

### AI query agent ([agent/spatial_agent.py](src/geohealth_etl/agent/spatial_agent.py))

The question and the table schema ([agent/prompt.py](src/geohealth_etl/agent/prompt.py)) go to `gpt-4o-mini`, which returns an explanation and a SQL query. The query is checked to be read-only (`SELECT`/`WITH` only, with no write keywords) before it runs. Matching tracts are returned as GeoJSON, filled yellow on the map, and the map zooms to them. The generated SQL is shown in the sidebar, and the returned rows appear in a **results table** under the map; click a row to zoom to that tract. Clickable **example questions** under the query box show the kinds of questions the agent can answer.

The agent returns at most **50 tracts** unless the question asks for all of them.

## Output table: `tract_food_mental_health`

One row per 2020 census tract in Middlesex County (357 tracts).

| Column | Description |
|---|---|
| `geoid`, `tract_name` | 11-digit tract FIPS code and name |
| `area_sqkm` | Tract area (km²) |
| `frequent_mental_distress_pct`, `depression_pct` | CDC PLACES adult prevalence (%) |
| `lila_1and10_flag`, `low_income_flag` | USDA low-income & low-access (food desert) flag; low-income flag |
| `poverty_rate`, `median_family_income` | USDA atlas socioeconomics |
| `healthy_outlet`, `less_healthy_outlet` | OSM outlet counts |
| `healthy_per_sqkm`, `less_healthy_per_sqkm` | Outlet densities |
| `mrfei_score` | 0–100; 0 when a tract has no outlets |
| `est_veg_meat_share_pct`, `est_veg_meat_zscore` | Model-estimated vegetable share and its county z-score |
| `cluster_id`, `cluster_label` | K-means profile (`-1` / `Insufficient Data` if excluded) |
| `lisa_cluster`, `lisa_pvalue` | Local Moran's I classification and pseudo p-value |
| `geometry` | MultiPolygon, SRID 4326 |

## API

| Endpoint | Description |
|---|---|
| `GET /` | Map explorer |
| `GET /api/tracts` | All tracts with cluster fields as a GeoJSON FeatureCollection |
| `POST /api/query` | Body `{"question": "..."}` → `{explanation, sql, match_count, geojson}` |

## Known limitations

- **USDA tract mismatch:** the 2019 Food Access Atlas uses 2010 tract IDs, so about 85 of 357 tracts (those split or renumbered in 2020) get no USDA values. Those tracts also have no median family income, so they're excluded from clustering and show as `Insufficient Data`. Mapping the IDs with the Census 2010→2020 tract relationship file would fix this.
- **Diet estimates are rough:** the NHANES model's individual-level cross-validated error is about 29 percentage points. Tract estimates reflect demographic composition only, not local food access.
- **mRFEI = 0 is ambiguous:** it can mean no healthy outlets, or no outlets at all. Check `healthy_outlet + less_healthy_outlet` before interpreting it.
- **The AI agent can be wrong:** review the generated SQL shown in the sidebar before relying on a result.
- **Not reproducible over time:** each `etl` run downloads current OpenStreetMap data, and the diet model retrains on every run.

## Troubleshooting

| Error | Fix |
|---|---|
| `Connection refused` on port 5432 | The database container stopped: `docker compose up -d` |
| `No module named 'psycopg'` | `DATABASE_URL` is missing the driver prefix; use `postgresql+psycopg2://...` |
| `CENSUS_API_KEY is not set` | Add the key to `.env`, and make sure you clicked the activation email |
| `OpenAIError: Missing credentials` | Add `OPENAI_API_KEY` to `.env` |
| `/api/tracts` errors with a missing `cluster_*` column | The table was built by an older version without clustering; run `uv run geohealth cluster` (or `etl`) |

## Project layout

```
src/geohealth_etl/
├── cli.py              # `geohealth` command: etl | cluster | serve
├── config.py           # Study area, CRS, env vars (.env loaded here)
├── db.py               # PostGIS read/write + indexes
├── etl/
│   ├── pipeline.py     # ETL orchestration
│   └── source.py       # TIGER, CDC PLACES, USDA, OSM fetchers
├── modeling/
│   ├── diet_estimator.py  # NHANES → ACS small-area diet model
│   └── cluster.py         # Moran's I, LISA, K-means
├── agent/
│   ├── prompt.py       # LLM system prompt / table schema
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
| **ERS** | Economic Research Service, the USDA agency that publishes the Food Access Research Atlas. |
| **NHANES** | National Health and Nutrition Examination Survey: a CDC survey that combines interviews, physical exams and detailed 24-hour dietary recalls. It's used here to train the diet model. |
| **OSM** | OpenStreetMap: a crowd-sourced world map, used here for food outlet locations. **OSMnx** is the Python library that downloads OSM data. |
| **TIGER/Line** | *Topologically Integrated Geographic Encoding and Referencing*: the Census Bureau's official boundary files, used here for census tract shapes. |
| **USDA** | United States Department of Agriculture. |

### Food environment and health measures

| Term | Meaning |
|---|---|
| **mRFEI** | *Modified Retail Food Environment Index*, a measure developed by the CDC: healthy food outlets ÷ (healthy + less-healthy outlets) × 100. Ranges from 0 to 100, and higher means a healthier mix. Here "healthy" is supermarkets, greengrocers and marketplaces, and "less healthy" is fast food and convenience stores, all from OSM. This is an approximation of the CDC definition, which uses commercial business listings. A score of 0 can also mean a tract has no outlets at all. |
| **LILA** | *Low Income and Low Access*, USDA's food-desert definition. `lila_1and10_flag` = 1 means a low-income tract where many residents live more than 1 mile (urban) or 10 miles (rural) from a supermarket. |
| **Food desert** | An area where healthy food is hard to reach. This project uses the USDA LILA flag as its food-desert measure. |
| **Food swamp** | An area where most food outlets are fast food or convenience stores (low mRFEI), even if supermarkets exist nearby. |
| **Frequent mental distress** | CDC PLACES measure: % of adults reporting 14 or more days of poor mental health in the past 30 days. |

### Spatial statistics and modeling

| Term | Meaning |
|---|---|
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
