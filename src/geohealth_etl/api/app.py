import json
from pathlib import Path
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import text
from geohealth_etl.db import engine
from geohealth_etl.agent.spatial_agent import ask_spatial_agent

app = FastAPI(title="GeoHealth AI Explorer")
STATIC_DIR = Path(__file__).parent / "static"


class QueryRequest(BaseModel):
    question: str


@app.get("/")
def serve_frontend():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/tracts")
def get_all_tracts():
    sql = """
        SELECT geoid, tract_name, frequent_mental_distress_pct, depression_pct,
               mrfei_score, median_family_income, healthy_outlet, less_healthy_outlet,
               cluster_id, cluster_label, lisa_cluster, lisa_pvalue,
               ST_AsGeoJSON(geometry)::json AS geojson
        FROM tract_food_mental_health
    """
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
