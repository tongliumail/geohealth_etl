import json
from pathlib import Path
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import text
from geohealth_etl.config import TABLE_NAME
from geohealth_etl.db import engine
from geohealth_etl.factors import (
    BURDEN_COL, BURDEN_PCTILE_COL, FACTORS, THEMES, catalogue_json, pctile_col, theme_col,
)
from geohealth_etl.agent.spatial_agent import ask_spatial_agent

app = FastAPI(title="GeoHealth AI Explorer")
STATIC_DIR = Path(__file__).parent / "static"


class QueryRequest(BaseModel):
    question: str


@app.get("/")
def serve_frontend():
    return FileResponse(STATIC_DIR / "index.html")


# Columns always sent to the map, besides the catalogue's factor/percentile/score columns
BASE_COLUMNS = [
    "geoid", "tract_name", "cluster_id", "cluster_label", "lisa_cluster", "lisa_pvalue",
    "median_family_income", "healthy_outlet", "less_healthy_outlet", "lila_1and10_flag", "ej_criteria",
]


def _map_columns() -> list[str]:
    """Catalogue + base columns that actually exist in the table (older tables lack new factors)."""
    wanted = BASE_COLUMNS + [f.key for f in FACTORS] + [pctile_col(f.key) for f in FACTORS]
    wanted += [theme_col(t) for t in THEMES] + [BURDEN_COL, BURDEN_PCTILE_COL]
    with engine.connect() as conn:
        existing = set(conn.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
            {"t": TABLE_NAME},
        ).scalars())
    return list(dict.fromkeys(c for c in wanted if c in existing))


@app.get("/api/factors")
def get_factor_catalogue():
    """Factor catalogue: themes, factors (label, unit, direction, source...), score columns."""
    return catalogue_json()


@app.get("/api/tracts")
def get_all_tracts():
    cols = ", ".join(f'"{c}"' for c in _map_columns())
    sql = f"SELECT {cols}, ST_AsGeoJSON(geometry)::json AS geojson FROM {TABLE_NAME}"
    with engine.connect() as conn:
        df = pd.read_sql(text(sql), conn)

    features = []
    for _, row in df.iterrows():
        geom = row["geojson"] if isinstance(row["geojson"], dict) else json.loads(row["geojson"])
        props = {k: (None if pd.isna(v) else v) for k, v in row.items() if k != "geojson"}
        features.append({"type": "Feature", "geometry": geom, "properties": props})

    return {"type": "FeatureCollection", "features": features}


@app.post("/api/query")
def run_ai_query(req: QueryRequest):
    try:
        return ask_spatial_agent(req.question)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
