import geopandas as gpd
from sqlalchemy import create_engine, text
from geoalchemy2 import Geometry
from geohealth_etl.config import DB_URL, TABLE_NAME

engine = create_engine(DB_URL)


def write_spatial_table(gdf: gpd.GeoDataFrame, table_name: str = TABLE_NAME) -> None:
    """Write a GeoDataFrame to PostGIS, rebuild GIST/GEOID indexes, and run VACUUM ANALYZE."""
    gdf.to_postgis(
        name=table_name,
        con=engine,
        if_exists="replace",
        index=False,
        dtype={"geometry": Geometry("GEOMETRY", srid=4326)},
    )

    with engine.begin() as conn:
        conn.execute(text(f"CREATE INDEX idx_{table_name}_geom ON {table_name} USING GIST (geometry);"))
        conn.execute(text(f"CREATE INDEX idx_{table_name}_geoid ON {table_name} (geoid);"))

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f"VACUUM ANALYZE {table_name};"))


def read_spatial_table(table_name: str = TABLE_NAME) -> gpd.GeoDataFrame:
    """Load the full spatial table from PostGIS into a GeoDataFrame."""
    return gpd.read_postgis(f"SELECT * FROM {table_name}", engine, geom_col="geometry")
