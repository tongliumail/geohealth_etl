from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from geohealth_etl.tract_data import load_factors, load_meta, load_tracts
from geohealth_etl.agent.spatial_agent import ask_spatial_agent

app = FastAPI(title="GeoHealth AI Explorer")
STATIC_DIR = Path(__file__).parent / "static"


class QueryRequest(BaseModel):
    question: str


@app.get("/")
def serve_frontend():
    return FileResponse(STATIC_DIR / "index.html")


# The page reads data/factors.json, data/tracts.geojson and data/meta.json. The static export writes
# the same files (see geohealth_etl/export.py), so one index.html works locally and on a static host.
@app.get("/data/factors.json")
def data_factors():
    return load_factors()


@app.get("/data/tracts.geojson")
def data_tracts():
    return load_tracts()


@app.get("/data/meta.json")
def data_meta():
    return load_meta()


# Older API paths, kept for API users
@app.get("/api/factors")
def get_factor_catalogue():
    return load_factors()


@app.get("/api/tracts")
def get_all_tracts():
    return load_tracts()


@app.post("/api/query")
def run_ai_query(req: QueryRequest):
    try:
        return ask_spatial_agent(req.question)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
