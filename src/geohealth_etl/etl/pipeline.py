import geopandas as gpd
from shapely.geometry import MultiPolygon
from geohealth_etl.config import (
    STATE_FIPS,
    COUNTY_FIPS,
    STATE_ABBR,
    LOCAL_CRS,
    STORAGE_CRS,
    TABLE_NAME,
)
from geohealth_etl.db import write_spatial_table
from geohealth_etl.etl.source import (
    fetch_census_tracts,
    fetch_cdc_places,
    fetch_usda_food_access,
    fetch_osm_food_pois,
)
from geohealth_etl.modeling.cluster import add_spatial_clusters
from geohealth_etl.modeling.diet_estimator import estimate_tract_veg_vs_meat


def run_pipeline() -> None:
    tracts = fetch_census_tracts(STATE_FIPS, COUNTY_FIPS)
    cdc_df = fetch_cdc_places(STATE_ABBR)
    usda_df = fetch_usda_food_access(STATE_FIPS)
    diet_df = estimate_tract_veg_vs_meat(STATE_FIPS, COUNTY_FIPS)
    osm_pois = fetch_osm_food_pois(tracts)

    print("5. Standardizing CRS and calculating tract areas...")
    tracts_proj = tracts.to_crs(LOCAL_CRS)
    tracts["area_sqkm"] = tracts_proj.geometry.area / 1_000_000.0
    tracts = tracts.to_crs(STORAGE_CRS)

    print("6. Joining tabular datasets and performing spatial point-in-polygon join...")
    gdf = tracts.merge(cdc_df, on="geoid", how="left")
    gdf = gdf.merge(usda_df, on="geoid", how="left")
    gdf = gdf.merge(diet_df, on="geoid", how="left")

    joined_pois = gpd.sjoin(osm_pois, gdf[["geoid", "geometry"]], how="inner", predicate="within")
    poi_counts = (
        joined_pois.groupby(["geoid", "food_category"])
        .size()
        .unstack(fill_value=0)
        .reset_index()
    )

    for col in ["healthy_outlet", "less_healthy_outlet"]:
        if col not in poi_counts.columns:
            poi_counts[col] = 0

    gdf = gdf.merge(poi_counts, on="geoid", how="left")
    gdf["healthy_outlet"] = gdf["healthy_outlet"].fillna(0).astype(int)
    gdf["less_healthy_outlet"] = gdf["less_healthy_outlet"].fillna(0).astype(int)

    print("7. Engineering density metrics and mRFEI score...")
    gdf["healthy_per_sqkm"] = gdf["healthy_outlet"] / gdf["area_sqkm"]
    gdf["less_healthy_per_sqkm"] = gdf["less_healthy_outlet"] / gdf["area_sqkm"]

    total_outlets = gdf["healthy_outlet"] + gdf["less_healthy_outlet"]
    gdf["mrfei_score"] = (100.0 * gdf["healthy_outlet"] / total_outlets).fillna(0.0)

    gdf = gdf[gdf.is_valid & ~gdf.is_empty].copy()
    gdf["geometry"] = gdf["geometry"].apply(
        lambda geom: geom if geom.geom_type == "MultiPolygon" else MultiPolygon([geom])
    )

    # Cluster before writing so the table always has the columns the map needs
    print("8. Running spatial clustering (Moran's I, LISA, K-means)...")
    gdf = add_spatial_clusters(gdf)

    print(f"9. Writing {len(gdf)} census tracts to PostGIS ({TABLE_NAME})...")
    write_spatial_table(gdf, TABLE_NAME)
    print("ETL pipeline complete!")
