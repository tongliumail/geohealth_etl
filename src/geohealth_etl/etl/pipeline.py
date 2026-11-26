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
from geohealth_etl.etl.factor_sources import (
    crosswalk_2010_to_2020,
    count_near_tract_centres,
    fetch_acs_population,
    fetch_ejscreen,
    fetch_massgis_ej,
    fetch_massgis_environment,
    fetch_svi,
    fetch_tree_canopy,
)
from geohealth_etl.modeling.cluster import add_spatial_clusters
from geohealth_etl.modeling.diet_estimator import estimate_tract_veg_vs_meat
from geohealth_etl.modeling.scores import add_scores


def run_pipeline() -> None:
    tracts = fetch_census_tracts(STATE_FIPS, COUNTY_FIPS)
    cdc_df = fetch_cdc_places(STATE_ABBR)

    # USDA's 2019 atlas uses 2010 tract IDs; re-key it to 2020 tracts so every tract gets values
    usda_2010 = fetch_usda_food_access(STATE_FIPS)
    usda_df = crosswalk_2010_to_2020(
        usda_2010, STATE_FIPS,
        numeric_cols=["poverty_rate", "median_family_income"],
        flag_cols=["lila_1and10_flag", "low_income_flag"],
    )

    diet_df = estimate_tract_veg_vs_meat(STATE_FIPS, COUNTY_FIPS)
    osm_pois = fetch_osm_food_pois(tracts)

    print("4C. Fetching contributing-factor layers...")
    acs_pop = fetch_acs_population(STATE_FIPS, COUNTY_FIPS)
    factor_tables = [
        acs_pop,
        fetch_massgis_ej(acs_pop),
        fetch_svi(),
        fetch_ejscreen(STATE_FIPS, COUNTY_FIPS),
        fetch_tree_canopy(tracts),
        fetch_massgis_environment(tracts),
    ]

    # Alcohol outlets come from the same OSM query as food; count them near each tract centre
    is_alcohol = osm_pois["food_category"] == "alcohol_outlet"
    alcohol_counts = count_near_tract_centres(tracts, osm_pois[is_alcohol])
    factor_tables.append(alcohol_counts.rename("alcohol_outlets_1km").rename_axis("geoid").reset_index())
    osm_pois = osm_pois[~is_alcohol]

    print("5. Standardizing CRS and calculating tract areas...")
    tracts_proj = tracts.to_crs(LOCAL_CRS)
    tracts["area_sqkm"] = tracts_proj.geometry.area / 1_000_000.0
    tracts = tracts.to_crs(STORAGE_CRS)

    print("6. Joining tabular datasets and performing spatial point-in-polygon join...")
    gdf = tracts.merge(cdc_df, on="geoid", how="left")
    gdf = gdf.merge(usda_df, on="geoid", how="left")
    gdf = gdf.merge(diet_df, on="geoid", how="left")
    for table in factor_tables:
        gdf = gdf.merge(table, on="geoid", how="left")

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

    print("8. Scoring factors: percentiles, theme scores, burden score...")
    gdf = add_scores(gdf)

    # Cluster before writing so the table always has the columns the map needs
    print("9. Running spatial clustering (Moran's I, LISA, K-means)...")
    gdf = add_spatial_clusters(gdf)

    print(f"10. Writing {len(gdf)} census tracts to PostGIS ({TABLE_NAME})...")
    write_spatial_table(gdf, TABLE_NAME)
    print("ETL pipeline complete!")
