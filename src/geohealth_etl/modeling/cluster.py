import geopandas as gpd
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
import libpysal
from esda.moran import Moran, Moran_Local
from geohealth_etl.config import TABLE_NAME
from geohealth_etl.db import read_spatial_table, write_spatial_table


CLUSTER_FEATURES = [
    "frequent_mental_distress_pct",
    "mrfei_score",
    "less_healthy_per_sqkm",
    "median_family_income",
]


def add_spatial_clusters(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Return a copy of the tract table with LISA hot-spot and K-means cluster columns.

    Tracts missing any clustering input are labelled "Insufficient Data" (cluster_id -1).
    """
    features = CLUSTER_FEATURES
    valid = gdf.dropna(subset=features).copy()

    # Spatial Weights & Global Moran's I
    w = libpysal.weights.Queen.from_dataframe(valid, use_index=False)
    w.transform = "r"
    y = valid["frequent_mental_distress_pct"].values

    moran = Moran(y, w, permutations=999)
    print(f"   Global Moran's I (Mental Distress): {moran.I:.3f} (p-value: {moran.p_sim:.4f})")

    # Local Moran's I (LISA Hot/Cold Spots & Spatial Outliers)
    print("   Computing Local Moran's I (LISA) clusters...")
    lisa = Moran_Local(y, w, permutations=999, seed=42)
    quadrant_labels = {
        1: "High-High (Hot Spot)",
        2: "Low-High (Low Outlier)",
        3: "Low-Low (Cold Spot)",
        4: "High-Low (High Outlier)",
    }

    valid["lisa_cluster"] = np.where(
        lisa.p_sim < 0.05,
        pd.Series(lisa.q, index=valid.index).map(quadrant_labels),
        "Not Significant",
    )
    valid["lisa_pvalue"] = lisa.p_sim

    # Multivariate K-Means Clustering
    print("   Running multivariate K-means clustering...")
    X_scaled = StandardScaler().fit_transform(valid[features])
    kmeans = KMeans(n_clusters=4, random_state=42, n_init=10)
    valid["cluster_id"] = kmeans.fit_predict(X_scaled)

    cluster_means = (
        valid.groupby("cluster_id")[["frequent_mental_distress_pct", "mrfei_score", "median_family_income"]]
        .mean()
        .sort_values("frequent_mental_distress_pct")
    )
    ordered_ids = cluster_means.index.tolist()
    label_map = {
        ordered_ids[0]: "Low Distress / Higher Income",
        ordered_ids[1]: "Moderate Distress / Mixed Food Access",
        ordered_ids[2]: "Elevated Distress / Dense Retail",
        ordered_ids[3]: "High Distress / Low Income & Food Swamp",
    }
    valid["cluster_label"] = valid["cluster_id"].map(label_map)

    cols_to_drop = ["cluster_id", "cluster_label", "lisa_cluster", "lisa_pvalue"]
    gdf = gdf.drop(columns=cols_to_drop, errors="ignore")
    gdf = gdf.merge(
        valid[["geoid", "cluster_id", "cluster_label", "lisa_cluster", "lisa_pvalue"]],
        on="geoid",
        how="left",
    )
    gdf["cluster_id"] = gdf["cluster_id"].fillna(-1).astype(int)
    gdf["cluster_label"] = gdf["cluster_label"].fillna("Insufficient Data")
    gdf["lisa_cluster"] = gdf["lisa_cluster"].fillna("Insufficient Data")
    return gdf


def run_spatial_clustering() -> None:
    """Re-cluster the existing PostGIS table without re-running the full ETL."""
    print("1. Loading spatial table from PostGIS...")
    gdf = read_spatial_table(TABLE_NAME)
    print("2. Running spatial clustering...")
    gdf = add_spatial_clusters(gdf)
    print(f"3. Writing {len(gdf)} census tracts to PostGIS ({TABLE_NAME})...")
    write_spatial_table(gdf, TABLE_NAME)
    print("Spatial clustering complete and saved to PostGIS!")
