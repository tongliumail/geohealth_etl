import sys
import uvicorn
from geohealth_etl.etl.pipeline import run_pipeline
from geohealth_etl.modeling.cluster import run_spatial_clustering


def main():
    if len(sys.argv) < 2:
        print("Usage: uv run geohealth [etl | cluster | serve]")
        sys.exit(1)

    command = sys.argv[1].lower()
    if command == "etl":
        run_pipeline()
    elif command == "cluster":
        run_spatial_clustering()
    elif command == "serve":
        uvicorn.run("geohealth_etl.api.app:app", host="127.0.0.1", port=8000, reload=True)
    else:
        print(f"Unknown command: {command}. Choose from: etl, cluster, serve")
        sys.exit(1)


if __name__ == "__main__":
    main()
