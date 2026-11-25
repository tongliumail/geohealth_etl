import io
import requests
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import cross_val_score

from geohealth_etl.config import CENSUS_API_KEY


def _load_xpt_from_url(url: str, cols: list[str]) -> pd.DataFrame:
    """Download a CDC NHANES .XPT (SAS Transport) file directly into a DataFrame."""
    print(f"   Downloading {url.split('/')[-1]}...")
    resp = requests.get(url, timeout=300)  # P_DR1IFF.xpt is ~120 MB
    resp.raise_for_status()
    df = pd.read_sas(io.BytesIO(resp.content), format="xport")
    return df[cols].copy()


def build_nhanes_training_data() -> pd.DataFrame:
    """Fetch NHANES 2017-2020 demographics and Day-1 individual food intake records."""
    base_url = "https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2017/DataFiles"

    # 1. Demographics: Adults 20+ with valid education (1-5) and income-to-poverty ratio
    demo = _load_xpt_from_url(
        f"{base_url}/P_DEMO.xpt",
        cols=["SEQN", "RIDAGEYR", "RIAGENDR", "RIDRETH3", "DMDEDUC2", "INDFMPIR"],
    )
    demo = demo[
        (demo["RIDAGEYR"] >= 20)
        & (demo["DMDEDUC2"].isin([1, 2, 3, 4, 5]))
        & (demo["INDFMPIR"].notna())
    ].copy()

    # 2. Individual Foods File (Day 1): Separate Meat (codes 2xxxx) vs Vegetables (codes 7xxxx)
    foods = _load_xpt_from_url(
        f"{base_url}/P_DR1IFF.xpt",
        cols=["SEQN", "DR1IFDCD", "DR1IGRMS", "WTDRD1PP"],
    )
    foods = foods.dropna(subset=["DR1IFDCD", "DR1IGRMS", "WTDRD1PP"])
    foods["food_prefix"] = foods["DR1IFDCD"].astype(int).astype(str).str[0]

    veg_meat = foods[foods["food_prefix"].isin(["2", "7"])].copy()
    veg_meat["category"] = np.where(veg_meat["food_prefix"] == "7", "veg_grams", "meat_grams")

    intake = (
        veg_meat.pivot_table(
            index=["SEQN", "WTDRD1PP"],
            columns="category",
            values="DR1IGRMS",
            aggfunc="sum",
            fill_value=0.0,
        )
        .reset_index()
    )

    # Merge demographics with dietary intake
    df = demo.merge(intake, on="SEQN", how="inner")
    total_grams = df["veg_grams"] + df["meat_grams"]
    df = df[total_grams > 0].copy()

    # Target: Vegetable share of combined (Vegetable + Meat) gram intake (0 to 100%)
    df["veg_share_pct"] = 100.0 * df["veg_grams"] / (df["veg_grams"] + df["meat_grams"])

    # Engineer harmonized features matching US Census ACS tract distributions
    df["age"] = df["RIDAGEYR"].astype(float)
    df["is_female"] = (df["RIAGENDR"] == 2).astype(float)
    df["is_college_grad"] = (df["DMDEDUC2"] == 5).astype(float)
    df["is_non_hispanic_white"] = (df["RIDRETH3"] == 3).astype(float)
    df["is_non_hispanic_black"] = (df["RIDRETH3"] == 4).astype(float)
    df["is_non_hispanic_asian"] = (df["RIDRETH3"] == 6).astype(float)
    df["is_hispanic"] = (df["RIDRETH3"].isin([1, 2])).astype(float)
    df["income_poverty_ratio"] = df["INDFMPIR"].clip(0, 5).astype(float)

    return df


def fetch_acs_tract_features(state_fips: str, county_fips: str) -> pd.DataFrame:
    """Fetch matching demographic distributions from the 2022 ACS 5-Year API."""
    print("   Fetching US Census ACS 5-Year tract demographics...")
    variables = [
        "B01002_001E",  # Median Age
        "B01001_001E", "B01001_026E",  # Total & Female Pop
        "B15003_001E", "B15003_022E", "B15003_023E", "B15003_024E", "B15003_025E",  # Education 25+
        "B03002_001E", "B03002_003E", "B03002_004E", "B03002_006E", "B03002_012E",  # Race/Ethnicity
        "C17002_001E", "C17002_002E", "C17002_003E", "C17002_008E",  # Income-to-Poverty Ratio
    ]
    if not CENSUS_API_KEY:
        raise RuntimeError(
            "CENSUS_API_KEY is not set. Get a free key at "
            "https://api.census.gov/data/key_signup.html and add it to .env"
        )
    url = "https://api.census.gov/data/2022/acs/acs5"
    params = {
        "get": ",".join(variables),
        "for": "tract:*",
        "in": f"state:{state_fips} county:{county_fips}",
        "key": CENSUS_API_KEY,
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    rows = resp.json()

    acs = pd.DataFrame(rows[1:], columns=rows[0])
    for col in variables:
        acs[col] = pd.to_numeric(acs[col], errors="coerce")

    acs["geoid"] = acs["state"] + acs["county"] + acs["tract"]

    features = pd.DataFrame()
    features["geoid"] = acs["geoid"]
    features["age"] = acs["B01002_001E"].where(acs["B01002_001E"] > 0)
    features["is_female"] = acs["B01001_026E"] / acs["B01001_001E"].replace(0, np.nan)

    college_grads = (
        acs["B15003_022E"] + acs["B15003_023E"] + acs["B15003_024E"] + acs["B15003_025E"]
    )
    features["is_college_grad"] = college_grads / acs["B15003_001E"].replace(0, np.nan)

    race_tot = acs["B03002_001E"].replace(0, np.nan)
    features["is_non_hispanic_white"] = acs["B03002_003E"] / race_tot
    features["is_non_hispanic_black"] = acs["B03002_004E"] / race_tot
    features["is_non_hispanic_asian"] = acs["B03002_006E"] / race_tot
    features["is_hispanic"] = acs["B03002_012E"] / race_tot

    pov_tot = acs["C17002_001E"].replace(0, np.nan)
    below_1 = (acs["C17002_002E"] + acs["C17002_003E"]) / pov_tot
    above_2 = acs["C17002_008E"] / pov_tot
    mid_1_to_2 = (1.0 - below_1 - above_2).clip(0, 1)
    features["income_poverty_ratio"] = (0.5 * below_1 + 1.5 * mid_1_to_2 + 3.8 * above_2).clip(0, 5)

    return features


def estimate_tract_veg_vs_meat(state_fips: str, county_fips: str) -> pd.DataFrame:
    """Train ML model on NHANES and predict tract-level Vegetable-to-Meat share."""
    print("4B. Running ML Small-Area Estimation (NHANES -> Census ACS)...")
    train_df = build_nhanes_training_data()

    feature_cols = [
        "age",
        "is_female",
        "is_college_grad",
        "is_non_hispanic_white",
        "is_non_hispanic_black",
        "is_non_hispanic_asian",
        "is_hispanic",
        "income_poverty_ratio",
    ]

    X = train_df[feature_cols]
    y = train_df["veg_share_pct"]
    weights = train_df["WTDRD1PP"]

    model = HistGradientBoostingRegressor(
        max_iter=150,
        learning_rate=0.05,
        max_depth=4,
        min_samples_leaf=30,
        random_state=42,
    )
    model.fit(X, y, sample_weight=weights)

    cv_mae = -cross_val_score(model, X, y, cv=5, scoring="neg_mean_absolute_error").mean()
    print(f"   NHANES 5-Fold Cross-Val MAE (Individual level): {cv_mae:.2f} percentage points")

    acs_df = fetch_acs_tract_features(state_fips, county_fips)
    valid_mask = acs_df[feature_cols].notna().all(axis=1)

    acs_df["est_veg_meat_share_pct"] = np.nan
    acs_df.loc[valid_mask, "est_veg_meat_share_pct"] = model.predict(
        acs_df.loc[valid_mask, feature_cols]
    )

    mean_share = acs_df["est_veg_meat_share_pct"].mean()
    std_share = acs_df["est_veg_meat_share_pct"].std()
    acs_df["est_veg_meat_zscore"] = (acs_df["est_veg_meat_share_pct"] - mean_share) / std_share

    return acs_df[["geoid", "est_veg_meat_share_pct", "est_veg_meat_zscore", "is_college_grad"]]

