"""Percentiles, theme scores and the overall environmental burden score.

Method (same idea as EPA EJScreen and the CDC Environmental Justice Index):
1. Each factor becomes a county percentile 0–100. For exposures the scale is flipped
   where needed so 100 always means the WORST condition (e.g. least tree canopy).
   For population and context factors, 100 = highest share or estimate.
2. A theme score is the mean of its exposure percentiles (ignoring missing values).
3. The burden score is the mean of the theme scores, so each theme counts equally
   no matter how many factors it has; burden_pctile ranks that score 0–100.
"""
import pandas as pd

from geohealth_etl.factors import (
    BURDEN_COL, BURDEN_PCTILE_COL, FACTORS, THEMES, pctile_col, theme_col,
)


def _pctile(s: pd.Series, worse_is_higher: bool = True) -> pd.Series:
    ranked = s.rank(pct=True, method="average", ascending=worse_is_higher)
    return (100.0 * ranked).round(1)


def add_scores(gdf: pd.DataFrame) -> pd.DataFrame:
    gdf = gdf.copy()
    for f in FACTORS:
        if f.key not in gdf.columns:
            print(f"   [Warning] factor column missing, skipped: {f.key}")
            continue
        worse_is_higher = f.higher_is_worse if f.role == "exposure" else True
        values = gdf[f.key].astype(float)
        if f.key == "mrfei_score" and {"healthy_outlet", "less_healthy_outlet"} <= set(gdf.columns):
            # mRFEI is 0 both for "only unhealthy outlets" and "no outlets at all". A tract with no
            # food outlets isn't a food swamp, so leave it unranked; its food theme score then
            # comes from the other food factors.
            values = values.where((gdf["healthy_outlet"] + gdf["less_healthy_outlet"]) > 0)
        gdf[pctile_col(f.key)] = _pctile(values, worse_is_higher)

    for theme in THEMES:
        cols = [pctile_col(f.key) for f in FACTORS
                if f.role == "exposure" and f.theme == theme and pctile_col(f.key) in gdf.columns]
        gdf[theme_col(theme)] = gdf[cols].mean(axis=1).round(1) if cols else pd.NA

    # Rank the unrounded average (rounding first creates artificial ties); the map page
    # recomputes the same ranking from the stored theme scores, so the two always agree.
    burden = gdf[[theme_col(t) for t in THEMES]].mean(axis=1)
    gdf[BURDEN_PCTILE_COL] = _pctile(burden)
    gdf[BURDEN_COL] = burden.round(1)
    return gdf
