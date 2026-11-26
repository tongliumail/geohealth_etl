"""Fetchers for the contributing-factor layers (demographics, green space, noise, air, services).

Environment layers come from MassGIS (state open space, MassDOT roads, MBTA, health facilities,
EJ populations); food and alcohol outlets come from OpenStreetMap (see etl/source.py).

Every function returns a DataFrame keyed by 11-digit 2020 tract `geoid`. Large raw
downloads are cached under DATA_CACHE_DIR so re-running the ETL doesn't re-download them.
"""
import io
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import rasterio.mask
import requests

from geohealth_etl.config import CENSUS_API_KEY, DATA_CACHE_DIR, LOCAL_CRS, STORAGE_CRS

ACS_URL = "https://api.census.gov/data/2022/acs/acs5"
SVI_URL = "https://svi.cdc.gov/Documents/Data/2022/csv/states/{state_name}.csv"
# EPA removed EJScreen in 2025; the Public Environmental Data Partners mirror on Harvard Dataverse
# (doi:10.7910/DVN/RLR5AX) hosts the 2024 tract file: EJScreen_2024_Tract_with_AS_CNMI_GU_VI.csv
EJSCREEN_TRACT_URL = "https://dataverse.harvard.edu/api/access/datafile/10775979"
CANOPY_WCS_URL = (
    "https://www.mrlc.gov/geoserver/ows?service=WCS&version=2.0.1&request=GetCoverage"
    "&coverageId=mrlc_download__nlcd_tcc_conus_2021_v2021-4"
    "&subset=Long({minx},{maxx})&subset=Lat({miny},{maxy})"
    "&subsettingCrs=http://www.opengis.net/def/crs/EPSG/0/4326&format=image/geotiff"
)
TRACT_2020_2010_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/rel2020/tract/tab20_tract20_tract10_st{state_fips}.txt"
)
NEARBY_RADIUS_M = 1000


def _cached_path(name: str) -> Path:
    path = Path(DATA_CACHE_DIR) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _download(url: str, name: str, timeout: int = 300) -> Path:
    """Download url to the cache once; later calls reuse the file."""
    path = _cached_path(name)
    if not path.exists():
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        path.write_bytes(resp.content)
    return path


# --------------------------------------------------------------------------- population
def fetch_acs_population(state_fips: str, county_fips: str) -> pd.DataFrame:
    """% people of color, % foreign-born, % limited-English households, % below poverty."""
    print("   Census ACS: race/ethnicity, nativity, language, poverty...")
    if not CENSUS_API_KEY:
        raise RuntimeError("CENSUS_API_KEY is not set (see README → Configuration).")
    v = [
        "B03002_001E", "B03002_003E",                                # total, non-Hispanic white
        "B05002_001E", "B05002_013E",                                # total, foreign-born
        "C16002_001E", "C16002_004E", "C16002_007E", "C16002_010E", "C16002_013E",  # households, LEP by language
        "B17001_001E", "B17001_002E",                                # poverty universe, below poverty
    ]
    resp = requests.get(ACS_URL, timeout=60, params={
        "get": ",".join(v), "for": "tract:*", "in": f"state:{state_fips} county:{county_fips}", "key": CENSUS_API_KEY,
    })
    resp.raise_for_status()
    rows = resp.json()
    acs = pd.DataFrame(rows[1:], columns=rows[0])
    acs[v] = acs[v].apply(pd.to_numeric, errors="coerce").where(lambda d: d >= 0)  # negative = Census missing codes

    def pct(num, den):
        return 100.0 * num / den.replace(0, np.nan)

    return pd.DataFrame({
        "geoid": acs["state"] + acs["county"] + acs["tract"],
        "total_population": acs["B03002_001E"],
        "pct_people_of_color": 100.0 - pct(acs["B03002_003E"], acs["B03002_001E"]),
        "pct_foreign_born": pct(acs["B05002_013E"], acs["B05002_001E"]),
        "pct_limited_english": pct(
            acs[["C16002_004E", "C16002_007E", "C16002_010E", "C16002_013E"]].sum(axis=1), acs["C16002_001E"]
        ),
        "pct_below_poverty": pct(acs["B17001_002E"], acs["B17001_001E"]),
    })


def fetch_svi(state_name: str = "Massachusetts") -> pd.DataFrame:
    print("   CDC Social Vulnerability Index 2022...")
    path = _download(SVI_URL.format(state_name=state_name), f"svi_2022_{state_name}.csv")
    svi = pd.read_csv(path, usecols=["FIPS", "RPL_THEMES"], dtype={"FIPS": str})
    svi["geoid"] = svi["FIPS"].str.zfill(11)
    svi["svi_overall"] = svi["RPL_THEMES"].where(svi["RPL_THEMES"] >= 0)  # -999 = missing
    return svi[["geoid", "svi_overall"]]


# --------------------------------------------------------------------------- air & traffic
def fetch_ejscreen(state_fips: str, county_fips: str) -> pd.DataFrame:
    """PM2.5, NO2, diesel PM and traffic proximity from EJScreen 2024 (2020 tracts)."""
    print("   EPA EJScreen 2024: air quality and traffic proximity...")
    prefix = state_fips + county_fips
    path = _cached_path(f"ejscreen_2024_tract_{prefix}.csv")
    if not path.exists():
        print("      first run: downloading the 153 MB national file and keeping this county...")
        tmp = path.with_suffix(".national.tmp")
        with requests.get(EJSCREEN_TRACT_URL, stream=True, timeout=600) as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as fh:
                for block in resp.iter_content(chunk_size=1 << 20):
                    fh.write(block)
        try:
            reader = pd.read_csv(tmp, usecols=["ID", "PM25", "NO2", "DSLPM", "PTRAF"],
                                 dtype={"ID": str}, chunksize=50000, encoding="utf-8-sig")
            subset = pd.concat(chunk[chunk["ID"].str.zfill(11).str.startswith(prefix)] for chunk in reader)
            subset.to_csv(path, index=False)
        finally:
            tmp.unlink(missing_ok=True)  # keep only the small county subset on disk
    df = pd.read_csv(path, dtype={"ID": str})
    return pd.DataFrame({
        "geoid": df["ID"].str.zfill(11),
        "pm25": df["PM25"], "no2": df["NO2"], "diesel_pm": df["DSLPM"], "traffic_proximity": df["PTRAF"],
    })


# --------------------------------------------------------------------------- tree canopy
def fetch_tree_canopy(tracts: gpd.GeoDataFrame) -> pd.DataFrame:
    """Mean NLCD 2021 tree canopy % per tract (30 m raster from the MRLC coverage service)."""
    print("   NLCD 2021 tree canopy (MRLC)...")
    t = tracts.to_crs(STORAGE_CRS)
    minx, miny, maxx, maxy = (round(v, 3) for v in t.total_bounds)
    pad = 0.01
    url = CANOPY_WCS_URL.format(minx=minx - pad, maxx=maxx + pad, miny=miny - pad, maxy=maxy + pad)
    path = _download(url, f"nlcd_tcc_2021_{minx}_{miny}_{maxx}_{maxy}.tif", timeout=600)

    values = []
    with rasterio.open(path) as src:
        t = t.to_crs(src.crs)
        for geom in t.geometry:
            # nodata=255 so 0 (= no canopy) stays a valid value; 254/255 are non-processing/background
            arr, _ = rasterio.mask.mask(src, [geom], crop=True, filled=True, nodata=255)
            valid = arr[(arr <= 100)]
            values.append(float(valid.mean()) if valid.size else np.nan)
    return pd.DataFrame({"geoid": tracts["geoid"].values, "tree_canopy_pct": values})


# --------------------------------------------------------------------------- MassGIS layers
# MassGIS (the state GIS office) publishes its layers in a public S3 bucket. mass.gov's own
# pages block automated requests, so we download the zipped shapefiles from the bucket directly.
MASSGIS_BUCKET = "https://s3.us-east-1.amazonaws.com/download.massgis.digital.mass.gov"
MASSGIS_LAYERS = {
    # name: (bucket key, shapefile inside the zip)
    "openspace": ("shapefiles/state/openspace.zip", "OPENSPACE_POLY.shp"),
    "roads": ("shapefiles/state/MassDOT_Roads_SHP.zip", "EOTROADS_ARC.shp"),
    "rail": ("shapefiles/state/trains.zip", "TRAINS_ARC.shp"),
    "rail_stations": ("shapefiles/state/trains.zip", "TRAINS_NODE.shp"),
    "rapid_transit": ("shapefiles/state/mbta_rapid_transit.zip", "MBTA_NODE.shp"),
    "bus_stops": ("shapefiles/state/mbtabus.zip", "mbtabus/MBTABUSSTOPS_PT.shp"),
    "libraries": ("shapefiles/state/libraries.zip", "LIBRARIES_PT.shp"),
    "worship": ("shapefiles/state/Places_of_Worship.zip", "Places_of_Worship/Places_of_Worship.shp"),
    "chcs": ("shapefiles/state/chcs.zip", "CHCS_PT.shp"),
    "acute_hospitals": ("shapefiles/state/acute_care_hospitals.zip", "HOSPITALS_PT.shp"),
    "nonacute_hospitals": ("shapefiles/state/non_acute_care_hospitals.zip", "HOSPITALS_NONACUTE_PT.shp"),
    "ej2020": ("shapefiles/census2020/ej2020.zip", "EJ_POLY.shp"),
}
RAIL_NOISE_BUFFER_M = 300


def read_massgis(name: str, bbox=None, columns=None) -> gpd.GeoDataFrame:
    """Read a MassGIS layer (downloaded once into data_cache/massgis/), in EPSG:26986.

    bbox (minx, miny, maxx, maxy in EPSG:26986) limits reading to the study area, which
    matters for the 400k-segment statewide road layer.
    """
    key, shp = MASSGIS_LAYERS[name]
    zip_path = _download(f"{MASSGIS_BUCKET}/{key}", f"massgis/{Path(key).name}", timeout=900)
    gdf = gpd.read_file(f"zip://{zip_path}!{shp}", bbox=bbox, columns=columns)
    return gdf.to_crs(LOCAL_CRS)


def _count_within(points: gpd.GeoDataFrame, buffers: gpd.GeoDataFrame) -> pd.Series:
    if len(points) == 0:
        return pd.Series(dtype=int)
    pts = gpd.GeoDataFrame(geometry=points.geometry.representative_point(), crs=LOCAL_CRS)
    return gpd.sjoin(pts, buffers, predicate="within").groupby("geoid").size()


def count_near_tract_centres(tracts: gpd.GeoDataFrame, points: gpd.GeoDataFrame,
                             radius_m: float = NEARBY_RADIUS_M) -> pd.Series:
    """Number of points within radius_m of each tract's centre, indexed by geoid."""
    t = tracts.to_crs(LOCAL_CRS)
    buffers = gpd.GeoDataFrame({"geoid": t["geoid"].values},
                               geometry=t.geometry.representative_point().buffer(radius_m).values, crs=LOCAL_CRS)
    counts = _count_within(points.to_crs(LOCAL_CRS), buffers)
    return t["geoid"].map(counts).fillna(0).astype(int).set_axis(t["geoid"])


def fetch_massgis_environment(tracts: gpd.GeoDataFrame) -> pd.DataFrame:
    """Open space, traffic volume, rail corridors, transit, health facilities and community places."""
    print("   MassGIS: open space, roads (traffic counts), rail, MBTA, health facilities, libraries, worship...")
    t = tracts.to_crs(LOCAL_CRS)[["geoid", "geometry"]].reset_index(drop=True)
    t_area = t.geometry.area
    centres = t.geometry.representative_point()
    pad = 5_000  # read a margin so features just over the county line still count
    minx, miny, maxx, maxy = t.total_bounds
    bbox = (minx - pad, miny - pad, maxx + pad, maxy + pad)
    out = pd.DataFrame({"geoid": t["geoid"]})

    # Green space: publicly accessible protected & recreational open space (Y = yes, L = limited)
    os_ = read_massgis("openspace", bbox=bbox, columns=["PUB_ACCESS", "PRIM_PURP"])
    os_ = os_[os_["PUB_ACCESS"].isin(["Y", "L"])]
    green = os_.geometry.make_valid().union_all()
    out["park_area_pct"] = (100.0 * t.geometry.intersection(green).area / t_area).clip(0, 100)
    out["dist_to_park_m"] = centres.distance(green)

    # Traffic noise proxy: daily vehicle-km travelled per km² (MassDOT AADT × segment length)
    roads = read_massgis("roads", bbox=bbox, columns=["AADT"])
    roads = roads[roads["AADT"] > 0]
    pieces = gpd.overlay(roads[["AADT", "geometry"]], t, how="intersection", keep_geom_type=True)
    vkt = (pieces["AADT"] * pieces.geometry.length / 1000.0).groupby(pieces["geoid"]).sum()
    out["traffic_vkt_per_sqkm"] = out["geoid"].map(vkt).fillna(0.0) / (t_area / 1e6)

    # Rail noise proxy: share of the tract within 300 m of an active railway
    rail = read_massgis("rail", bbox=bbox, columns=["COMMRAIL", "FREIGHT_OP", "PASS_OP", "RAIL_TRL", "DATE_ABAND"])
    active = (
        (rail["COMMRAIL"].eq("Y") | rail["FREIGHT_OP"].notna() | rail["PASS_OP"].notna())
        & rail["RAIL_TRL"].isna() & rail["DATE_ABAND"].fillna(0).eq(0)
    )
    rail_zone = rail[active].geometry.buffer(RAIL_NOISE_BUFFER_M).union_all()
    out["rail_corridor_pct"] = (100.0 * t.geometry.intersection(rail_zone).area / t_area).clip(0, 100)

    # Health care: distance to the nearest community health centre or hospital
    health = pd.concat([read_massgis(n) [["geometry"]] for n in ("chcs", "acute_hospitals", "nonacute_hospitals")])
    out["dist_to_health_facility_m"] = centres.distance(health.geometry.union_all())

    # Transit and community places within 1 km of the tract centre
    transit = pd.concat([read_massgis(n, bbox=bbox)[["geometry"]] for n in ("bus_stops", "rapid_transit", "rail_stations")])
    community = pd.concat([read_massgis(n, bbox=bbox)[["geometry"]] for n in ("libraries", "worship")])
    out["transit_stops_1km"] = out["geoid"].map(count_near_tract_centres(t, gpd.GeoDataFrame(transit, crs=LOCAL_CRS))).values
    out["community_places_1km"] = out["geoid"].map(count_near_tract_centres(t, gpd.GeoDataFrame(community, crs=LOCAL_CRS))).values
    return out


def fetch_massgis_ej(tract_population: pd.DataFrame) -> pd.DataFrame:
    """% of tract residents living in a state-designated Environmental Justice block group (MA EJ 2020).

    tract_population: DataFrame with geoid and total_population (ACS).
    """
    print("   MassGIS: 2020 Environmental Justice populations...")
    ej = read_massgis("ej2020", columns=["GEOID", "TOTAL_POP", "EJ", "EJ_CRIT_DE"])
    ej = ej[ej["EJ"].eq("Yes")]
    tract_id = ej["GEOID"].astype(str).str[:11]
    ej_pop = ej.groupby(tract_id)["TOTAL_POP"].sum()

    # Readable summary per tract, e.g. "3 block groups: Minority (2); Minority and income (1)"
    def describe(group: pd.DataFrame) -> str:
        counts = group["EJ_CRIT_DE"].value_counts()
        n = len(group)
        # Only show per-criterion counts when the block groups qualify for different reasons
        parts = "; ".join(f"{crit} ({k})" if len(counts) > 1 else crit for crit, k in counts.items())
        return f"{n} block group{'s' if n > 1 else ''}: {parts}"

    ej_desc = ej.groupby(tract_id)[["EJ_CRIT_DE"]].apply(describe)

    out = tract_population[["geoid", "total_population"]].copy()
    out["pct_in_ej_block_group"] = (
        100.0 * out["geoid"].map(ej_pop).fillna(0) / out["total_population"].replace(0, np.nan)
    ).clip(0, 100)
    out["ej_criteria"] = out["geoid"].map(ej_desc).fillna("None designated")
    return out[["geoid", "pct_in_ej_block_group", "ej_criteria"]]


# --------------------------------------------------------------------------- 2010 → 2020 tract crosswalk
def crosswalk_2010_to_2020(df_2010: pd.DataFrame, state_fips: str,
                           numeric_cols: list[str], flag_cols: list[str]) -> pd.DataFrame:
    """Re-key 2010-tract data (e.g. the USDA Food Access Atlas) to 2020 tracts.

    Each 2020 tract takes the land-area-weighted average of the 2010 tracts it overlaps.
    0/1 flags are set to 1 when at least half of the tract's land had the flag.
    """
    path = _download(TRACT_2020_2010_URL.format(state_fips=state_fips), f"tract20_tract10_st{state_fips}.txt")
    rel = pd.read_csv(path, sep="|", encoding="utf-8-sig",
                      usecols=["GEOID_TRACT_20", "GEOID_TRACT_10", "AREALAND_PART"],
                      dtype={"GEOID_TRACT_20": str, "GEOID_TRACT_10": str})
    rel = rel[rel["AREALAND_PART"] > 0]
    m = rel.merge(df_2010, left_on="GEOID_TRACT_10", right_on="geoid", how="inner")

    result = {}
    for col in numeric_cols + flag_cols:
        part = m.dropna(subset=[col])
        w = part["AREALAND_PART"]
        result[col] = (part[col] * w).groupby(part["GEOID_TRACT_20"]).sum() / w.groupby(part["GEOID_TRACT_20"]).sum()
    out = pd.DataFrame(result)
    for col in flag_cols:
        out[col] = (out[col] >= 0.5).astype("Int64").where(out[col].notna())
    return out.rename_axis("geoid").reset_index()
