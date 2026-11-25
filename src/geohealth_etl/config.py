import os

from dotenv import load_dotenv

# Load OPENAI_API_KEY, CENSUS_API_KEY, DATABASE_URL from the project-root .env
load_dotenv()

DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://geo_user:geo_password@localhost:5432/geohealth",
)
# Free key from https://api.census.gov/data/key_signup.html (required by the ACS API)
CENSUS_API_KEY = os.getenv("CENSUS_API_KEY")

TABLE_NAME = "tract_food_mental_health"

# Geographic Scope: Middlesex County, MA
STATE_FIPS = "25"
COUNTY_FIPS = "017"
STATE_ABBR = "MA"
OSM_PLACE = "Middlesex County, Massachusetts, USA"

# Coordinate Reference Systems
LOCAL_CRS = "EPSG:26986"    # NAD83 / Massachusetts Mainland (meters)
STORAGE_CRS = "EPSG:4326"   # WGS84 (lat/lon for PostGIS & MapLibre)
