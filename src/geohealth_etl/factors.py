"""Factor catalogue: the single description of every tract measure.

The ETL, the percentile/burden scoring, the map's menus and tract profile, and the
AI agent's schema prompt are all generated from this list. To add a factor, add an
entry here and a fetch step that produces its column.

Roles
-----
- "exposure":   an environmental condition (scored into themes and the burden index)
- "population": who lives in the tract (used for "who's affected" comparisons)
- "context":    modelled health estimates, shown for context only, never scored

Kinds
-----
- "measured": directly observed or counted
- "proxy":    a stand-in for something not measured directly (e.g. traffic for noise)
- "modelled": a statistical estimate (e.g. CDC PLACES, built from demographics)
"""
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Factor:
    key: str                # column name in the PostGIS table
    label: str              # short display name
    role: str               # exposure | population | context
    theme: str | None       # exposure theme key (see THEMES); None for other roles
    unit: str               # "%", "per km²", "m", "µg/m³", "ppb", "index", ...
    higher_is_worse: bool   # exposure direction; for population/context: higher = more
    kind: str               # measured | proxy | modelled
    source: str             # data source shown in the UI
    description: str        # one sentence for the tract profile and the AI prompt


THEMES = {
    "food": "Food & alcohol retail",
    "green": "Green space & nature",
    "noise": "Traffic & noise",
    "air": "Air quality",
    "services": "Services & community",
}

FACTORS: list[Factor] = [
    # ---------------- Exposures: food & alcohol retail ----------------
    Factor("mrfei_score", "Healthy food retail mix (mRFEI)", "exposure", "food", "index 0–100", False, "measured",
           "OpenStreetMap", "Healthy outlets as a share of all food outlets (higher = healthier mix)."),
    Factor("less_healthy_per_sqkm", "Fast food & convenience density", "exposure", "food", "per km²", True, "measured",
           "OpenStreetMap", "Fast-food restaurants and convenience stores per km² of tract."),
    Factor("alcohol_outlets_1km", "Alcohol outlets nearby", "exposure", "food", "within 1 km", True, "measured",
           "OpenStreetMap", "Liquor stores, bars and pubs within 1 km of the tract's centre."),

    # ---------------- Exposures: green space & nature ----------------
    Factor("tree_canopy_pct", "Tree canopy", "exposure", "green", "%", False, "measured",
           "USFS/MRLC NLCD Tree Canopy 2021", "Average tree canopy cover across the tract (30 m satellite data)."),
    Factor("park_area_pct", "Public open space", "exposure", "green", "%", False, "measured",
           "MassGIS Protected & Recreational Open Space",
           "Share of the tract that is publicly accessible parks, conservation land or recreation areas."),
    Factor("dist_to_park_m", "Distance to public open space", "exposure", "green", "m", True, "measured",
           "MassGIS Protected & Recreational Open Space",
           "Straight-line distance from the tract's centre to the nearest publicly accessible open space (0 if inside)."),

    # ---------------- Exposures: traffic & noise ----------------
    Factor("traffic_proximity", "Traffic proximity & volume", "exposure", "noise", "index", True, "proxy",
           "EPA EJScreen 2024 (via Harvard Dataverse)",
           "Daily traffic count at major roads within 500 m, divided by distance. Used as a road-noise proxy."),
    Factor("traffic_vkt_per_sqkm", "Traffic volume density", "exposure", "noise", "veh-km/day/km²", True, "proxy",
           "MassDOT Road Inventory (MassGIS)",
           "Daily vehicle-kilometres driven inside the tract per km² (traffic counts × road length). Used as a road-noise proxy."),
    Factor("rail_corridor_pct", "Near an active railway", "exposure", "noise", "%", True, "proxy",
           "MassGIS Trains layer", "Share of the tract within 300 m of an active commuter or freight railway. Used as a rail-noise proxy."),

    # ---------------- Exposures: air quality ----------------
    Factor("pm25", "Fine particulate matter (PM2.5)", "exposure", "air", "µg/m³", True, "modelled",
           "EPA EJScreen 2024 (via Harvard Dataverse)", "Annual average PM2.5 concentration (EPA air-quality model)."),
    Factor("no2", "Nitrogen dioxide (NO₂)", "exposure", "air", "ppbv", True, "modelled",
           "EPA EJScreen 2024 (via Harvard Dataverse)", "Annual average NO₂, mostly from traffic combustion."),
    Factor("diesel_pm", "Diesel particulate matter", "exposure", "air", "µg/m³", True, "modelled",
           "EPA EJScreen 2024 (via Harvard Dataverse)", "Diesel exhaust particulate concentration (EPA AirToxScreen)."),

    # ---------------- Exposures: services & community ----------------
    Factor("dist_to_health_facility_m", "Distance to health centre or hospital", "exposure", "services", "m", True,
           "measured", "MassGIS (DPH community health centres, acute & non-acute hospitals)",
           "Straight-line distance from the tract's centre to the nearest community health centre or hospital."),
    Factor("transit_stops_1km", "Transit stops nearby", "exposure", "services", "within 1 km", False, "measured",
           "MassGIS (MBTA bus stops, rapid transit and commuter rail stations)",
           "MBTA bus stops, subway and commuter-rail stations within 1 km of the tract's centre."),
    Factor("community_places_1km", "Libraries & places of worship nearby", "exposure", "services", "within 1 km", False,
           "measured", "MassGIS (Libraries, Places of Worship)",
           "Public libraries and places of worship within 1 km of the tract's centre."),

    # ---------------- Population: who lives here ----------------
    Factor("pct_people_of_color", "People of color", "population", None, "%", True, "measured",
           "Census ACS 2022 5-year (B03002)", "Share of residents who are not non-Hispanic white."),
    Factor("pct_foreign_born", "Foreign-born (immigrants)", "population", None, "%", True, "measured",
           "Census ACS 2022 5-year (B05002)", "Share of residents born outside the United States."),
    Factor("pct_limited_english", "Limited-English households", "population", None, "%", True, "measured",
           "Census ACS 2022 5-year (C16002)", "Households where no one aged 14+ speaks English very well."),
    Factor("pct_below_poverty", "Below poverty line", "population", None, "%", True, "measured",
           "Census ACS 2022 5-year (B17001)", "Share of residents with income below the federal poverty line."),
    Factor("pct_in_ej_block_group", "In a state EJ population area", "population", None, "%", True, "measured",
           "MassGIS 2020 Environmental Justice Populations",
           "Share of residents living in block groups Massachusetts designates as Environmental Justice populations "
           "(by minority share, income and/or limited English)."),
    Factor("svi_overall", "Social Vulnerability Index", "population", None, "percentile 0–1", True, "modelled",
           "CDC/ATSDR SVI 2022", "CDC's overall social vulnerability ranking within Massachusetts (1 = most vulnerable)."),

    # ---------------- Context: modelled mental-health estimates ----------------
    Factor("frequent_mental_distress_pct", "Frequent mental distress (est.)", "context", None, "%", True, "modelled",
           "CDC PLACES", "Estimated % of adults with 14+ poor mental-health days a month; modelled largely from demographics."),
    Factor("depression_pct", "Depression (est.)", "context", None, "%", True, "modelled",
           "CDC PLACES", "Estimated % of adults ever diagnosed with depression; modelled largely from demographics."),
    Factor("loneliness_pct", "Loneliness (est.)", "context", None, "%", True, "modelled",
           "CDC PLACES", "Estimated % of adults who feel lonely; modelled largely from demographics."),
]

FACTORS_BY_KEY = {f.key: f for f in FACTORS}


def pctile_col(key: str) -> str:
    """Column holding a factor's 0–100 percentile (for exposures, 100 = worst)."""
    return f"{key}_pctile"


def theme_col(theme: str) -> str:
    """Column holding a theme's 0–100 burden score (100 = worst)."""
    return f"theme_{theme}_score"


BURDEN_COL = "burden_score"
BURDEN_PCTILE_COL = "burden_pctile"


def catalogue_json() -> dict:
    """Catalogue as served to the web page by GET /api/factors."""
    return {
        "themes": [{"key": k, "label": v, "score_col": theme_col(k)} for k, v in THEMES.items()],
        "factors": [{**asdict(f), "pctile_col": pctile_col(f.key)} for f in FACTORS],
        "burden_col": BURDEN_COL,
        "burden_pctile_col": BURDEN_PCTILE_COL,
    }
