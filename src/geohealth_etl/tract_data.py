"""Builds the data the map page reads: tract GeoJSON, factor catalogue and metadata.

Used by both the local server (`geohealth serve`, served under /data/...) and the static
export (`geohealth export`, written to site/data/...), so the two always match.
"""
from datetime import date, datetime, timezone

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import MultiPolygon, mapping
from sqlalchemy import text

from geohealth_etl.config import LOCAL_CRS, STORAGE_CRS, TABLE_NAME
from geohealth_etl.db import engine
from geohealth_etl.factors import (
    BURDEN_COL, BURDEN_PCTILE_COL, FACTORS, THEMES, catalogue_json, pctile_col, theme_col,
)

# Columns the page needs besides the catalogue's factor / percentile / score columns
BASE_COLUMNS = [
    "geoid", "tract_name", "cluster_id", "cluster_label", "lisa_cluster", "lisa_pvalue",
    "median_family_income", "healthy_outlet", "less_healthy_outlet", "lila_1and10_flag",
    "ej_criteria", "total_population",
]

# Data sources not tied to a single factor (factor sources come from the catalogue)
OTHER_SOURCES = [
    "US Census TIGER/Line 2023 tract boundaries",
    "USDA Food Access Research Atlas 2019 (mapped to 2020 tracts)",
    "NHANES 2017–Mar 2020 + Census ACS 2022 (diet estimate)",
]


def map_columns() -> list[str]:
    """Catalogue + base columns that exist in the table (older tables may lack new factors)."""
    wanted = BASE_COLUMNS + [f.key for f in FACTORS] + [pctile_col(f.key) for f in FACTORS]
    wanted += [theme_col(t) for t in THEMES] + [BURDEN_COL, BURDEN_PCTILE_COL]
    with engine.connect() as conn:
        existing = set(conn.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
            {"t": TABLE_NAME},
        ).scalars())
    return list(dict.fromkeys(c for c in wanted if c in existing))


def _clean(value, decimals: int):
    """JSON-friendly scalar: NaN -> None, numpy -> Python, floats rounded."""
    if value is None or (isinstance(value, float) and np.isnan(value)) or value is pd.NA:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        v = float(value)
        return None if np.isnan(v) else round(v, decimals)
    return value


def load_tracts(simplify_m: float | None = None, coord_decimals: int | None = None,
                value_decimals: int = 3) -> dict:
    """Tract FeatureCollection for the map.

    simplify_m: if set, outlines are simplified with a tolerance in metres using
        shapely.coverage_simplify, which simplifies the tracts *together* so shared borders
        stay identical (no gaps or overlaps between neighbours).
    coord_decimals: round coordinates (5 decimals ≈ 1 m), applied to shared vertices alike.
    """
    cols = ", ".join(f'"{c}"' for c in map_columns())
    gdf = gpd.read_postgis(f"SELECT {cols}, geometry FROM {TABLE_NAME} ORDER BY geoid", engine, geom_col="geometry")

    geoms = gdf.geometry
    if simplify_m:
        projected = geoms.to_crs(LOCAL_CRS)
        simplified = shapely.coverage_simplify(projected.values, tolerance=simplify_m)
        geoms = gpd.GeoSeries(simplified, crs=LOCAL_CRS, index=gdf.index).to_crs(STORAGE_CRS)
    if coord_decimals is not None:
        geoms = gpd.GeoSeries(
            shapely.transform(geoms.values, lambda xy: np.round(xy, coord_decimals)), crs=STORAGE_CRS, index=gdf.index
        )
    geoms = geoms.apply(lambda g: g if g.geom_type == "MultiPolygon" else MultiPolygon([g]))

    props = gdf.drop(columns="geometry")
    features = []
    for (_, row), geom in zip(props.iterrows(), geoms):
        features.append({
            "type": "Feature",
            "geometry": mapping(geom),
            "properties": {k: _clean(v, value_decimals) for k, v in row.items()},
        })
    return {"type": "FeatureCollection", "features": features}


def load_factors() -> dict:
    return catalogue_json()


def load_meta(n_tracts: int | None = None) -> dict:
    """When and from what the data was built, shown on the page."""
    with engine.connect() as conn:
        count = n_tracts or conn.execute(text(f"SELECT count(*) FROM {TABLE_NAME}")).scalar()
    sources = list(dict.fromkeys(OTHER_SOURCES + [f.source for f in FACTORS]))
    return {
        "title": "Middlesex Environmental Burden Explorer",
        "area": "Middlesex County, Massachusetts",
        "tracts": count,
        "data_as_of": date.today().isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": sources,
    }
