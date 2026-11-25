import json
import pandas as pd
from pydantic import BaseModel, Field
from sqlalchemy import text
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from geohealth_etl.db import engine
from geohealth_etl.agent.prompt import SYSTEM_PROMPT


class SpatialSQLQuery(BaseModel):
    explanation: str = Field(
        description="Brief plain-English explanation of how the query filters or ranks the census tracts."
    )
    sql_query: str = Field(
        description=(
            "A valid PostgreSQL/PostGIS SELECT query against `tract_food_mental_health`. "
            "MUST include `geoid`, `tract_name`, and `ST_AsGeoJSON(geometry)::json AS geojson` in the SELECT clause."
        )
    )


def build_agent():
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    structured_llm = llm.with_structured_output(SpatialSQLQuery)
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", "{question}"),
    ])
    return prompt | structured_llm


def validate_read_only_sql(sql: str) -> None:
    cleaned = sql.strip().upper()
    if not cleaned.startswith("SELECT") and not cleaned.startswith("WITH"):
        raise ValueError("Only SELECT or WITH queries are permitted.")
    forbidden = ["DROP ", "DELETE ", "UPDATE ", "INSERT ", "ALTER ", "TRUNCATE ", "GRANT "]
    if any(keyword in cleaned for keyword in forbidden):
        raise ValueError("Forbidden SQL keyword detected.")


def ask_spatial_agent(question: str) -> dict:
    chain = build_agent()
    plan: SpatialSQLQuery = chain.invoke({"question": question})
    validate_read_only_sql(plan.sql_query)

    with engine.connect() as conn:
        df = pd.read_sql(text(plan.sql_query), conn)

    features = []
    for _, row in df.iterrows():
        geom = row.get("geojson")
        if isinstance(geom, str):
            geom = json.loads(geom)
        props = {k: (None if pd.isna(v) else v) for k, v in row.items() if k != "geojson"}
        features.append({
            "type": "Feature",
            "geometry": geom,
            "properties": props,
        })

    return {
        "question": question,
        "explanation": plan.explanation,
        "sql": plan.sql_query,
        "match_count": len(features),
        "geojson": {
            "type": "FeatureCollection",
            "features": features,
        },
    }
