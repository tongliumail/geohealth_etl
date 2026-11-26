import io
import zipfile
import requests
import pandas as pd
import geopandas as gpd
import osmnx as ox
from geohealth_etl.config import LOCAL_CRS, STORAGE_CRS

# The county's convex hull is ~3,125 km², just over OSMnx's 2,500 km² default, which would split
# the query into several Overpass requests that each wait for a public-server rate-limit slot.
ox.settings.max_query_area_size = 4_000_000_000
ox.settings.requests_timeout = 300


def fetch_census_tracts(state_fips: str, county_fips: str) -> gpd.GeoDataFrame:
    print("1. Fetching US Census TIGER/Line tract geometries...")
    tiger_url = f"https://www2.census.gov/geo/tiger/TIGER2023/TRACT/tl_2023_{state_fips}_tract.zip"
    tracts = gpd.read_file(tiger_url)
    tracts = tracts[tracts["COUNTYFP"] == county_fips].copy()
    tracts["geoid"] = tracts["GEOID"].astype(str).str.zfill(11)
    return tracts[["geoid", "NAMELSAD", "geometry"]].rename(columns={"NAMELSAD": "tract_name"})


def fetch_cdc_places(state_abbr: str) -> pd.DataFrame:
    print("2. Fetching CDC PLACES mental health estimates...")
    # CDC PLACES: Local Data for Better Health, Census Tract Data (Socrata API)
    cdc_url = "https://data.cdc.gov/resource/cwsq-ngmh.csv"
    params = {
        "$where": f"stateabbr='{state_abbr}' AND measureid in('MHLTH', 'DEPRESSION', 'LONELINESS')",
        "$select": "locationname, measureid, data_value",
        "$limit": 50000,
    }
    resp = requests.get(cdc_url, params=params, timeout=30)
    resp.raise_for_status()

    df = pd.read_csv(io.StringIO(resp.text))
    df["geoid"] = df["locationname"].astype(str).str.zfill(11)

    # Pivot measures into separate columns: mhlth (Frequent Mental Distress %), depression (%)
    pivoted = df.pivot_table(
        index="geoid", columns="measureid", values="data_value", aggfunc="first"
    ).reset_index()
    pivoted.columns.name = None
    return pivoted.rename(
        columns={
            "MHLTH": "frequent_mental_distress_pct",
            "DEPRESSION": "depression_pct",
            "LONELINESS": "loneliness_pct",
        }
    )


def fetch_usda_food_access(state_fips: str) -> pd.DataFrame:
    print("3. Fetching USDA Food Access Research Atlas data...")
    # Zip contains several CSVs; the data lives in "Food Access Research Atlas.csv"
    usda_url = "https://www.ers.usda.gov/media/5627/food-access-research-atlas-data-download-2019.zip"
    try:
        cols = ["CensusTract", "LILATracts_1And10", "LowIncomeTracts", "PovertyRate", "MedianFamilyIncome"]
        resp = requests.get(usda_url, timeout=120)
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            with zf.open("Food Access Research Atlas.csv") as f:
                df = pd.read_csv(f, usecols=cols, dtype={"CensusTract": str})
        df["geoid"] = df["CensusTract"].str.zfill(11)
        df = df[df["geoid"].str.startswith(state_fips)].copy()
        return df.drop(columns=["CensusTract"]).rename(
            columns={
                "LILATracts_1And10": "lila_1and10_flag",
                "LowIncomeTracts": "low_income_flag",
                "PovertyRate": "poverty_rate",
                "MedianFamilyIncome": "median_family_income",
            }
        )
    except Exception as e:
        print(f"   [Warning] USDA direct download skipped ({e}). Returning empty fallback.")
        return pd.DataFrame(columns=["geoid", "lila_1and10_flag", "low_income_flag", "poverty_rate", "median_family_income"])


def fetch_osm_food_pois(boundary_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    print("4. Fetching OpenStreetMap food and alcohol retail POIs via OSMnx...")
    # One query for food and alcohol outlets (MassGIS has no layer for either)
    tags = {
        "amenity": ["fast_food", "marketplace", "bar", "pub"],
        "shop": ["convenience", "supermarket", "greengrocer", "alcohol"],
    }
    # The geocoded county boundary has so many vertices that Overpass rejects the
    # query (413). Query a simple convex hull instead; the later sjoin clips to tracts.
    hull = boundary_gdf.to_crs(STORAGE_CRS).union_all().convex_hull
    pois = ox.features_from_polygon(hull, tags=tags)
    pois = pois.reset_index()

    # Classify into healthy vs. less-healthy food retail proxies, plus alcohol outlets
    def classify_food(row):
        shop = str(row.get("shop", ""))
        amenity = str(row.get("amenity", ""))
        if shop == "alcohol" or amenity in ["bar", "pub"]:
            return "alcohol_outlet"
        if shop in ["supermarket", "greengrocer"] or amenity == "marketplace":
            return "healthy_outlet"
        if amenity == "fast_food" or shop == "convenience":
            return "less_healthy_outlet"
        return "other"

    pois["food_category"] = pois.apply(classify_food, axis=1)
    pois = pois[pois["food_category"] != "other"].copy()

    # Convert building polygon footprints to centroids so all POIs are uniform Points
    pois_proj = pois.to_crs(LOCAL_CRS)
    pois_proj["geometry"] = pois_proj.geometry.centroid
    return pois_proj[["food_category", "geometry"]].to_crs(STORAGE_CRS)
