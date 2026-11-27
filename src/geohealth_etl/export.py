"""Static-site export: `geohealth export [--out site] [--simplify 10]`.

Writes a self-contained folder that any static host can serve (GitHub Pages, Netlify,
Cloudflare Pages, a city web server):

    site/
    ├── index.html             the map page (same file the local server uses)
    └── data/
        ├── tracts.geojson     every tract: simplified outline + all factor values, percentiles, scores
        ├── factors.json       factor catalogue (labels, themes, units, sources)
        └── meta.json          "data as of" date and source list

No database, server or API key is needed to view it. Re-run `geohealth etl` then
`geohealth export` to refresh the numbers.

`geohealth export --single-file` instead writes ONE html file with the data embedded
(site/geohealth-map.html). It opens by double-click, as an email attachment or from a shared
drive, because browsers block a page opened from disk (file://) from loading separate data
files. It still needs internet for the MapLibre library and basemap tiles.
"""
import argparse
import gzip
import json
import shutil
from pathlib import Path

from geohealth_etl.config import PROJECT_ROOT
from geohealth_etl.tract_data import load_factors, load_meta, load_tracts

PAGE = Path(__file__).parent / "api" / "static" / "index.html"


def _write_json(path: Path, data: dict) -> tuple[int, int]:
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    return len(raw), len(gzip.compress(raw))


SINGLE_FILE_NAME = "geohealth-map.html"


def _embed_json(data: dict) -> str:
    """JSON safe to place inside a <script> tag ("</script>" in a value must not end the tag)."""
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")


def run_single_file_export(out_dir: str | Path = PROJECT_ROOT / "site", simplify_m: float = 10.0) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    print(f"1. Loading tracts from PostGIS and simplifying outlines ({simplify_m:g} m tolerance)...")
    tracts = load_tracts(simplify_m=simplify_m or None, coord_decimals=5)
    data_js = (
        "<script>\n// Data embedded by `geohealth export --single-file`; the page uses it instead of fetching data/ files\n"
        f"window.GEOHEALTH_DATA = {{\n\"factors\": {_embed_json(load_factors())},\n"
        f"\"meta\": {_embed_json(load_meta(len(tracts['features'])))},\n"
        f"\"tracts\": {_embed_json(tracts)}\n}};\n</script>\n"
    )
    page = PAGE.read_text(encoding="utf-8")
    marker = "  <script>\n"  # the page's own script; data must be defined before it runs
    if marker not in page:
        raise RuntimeError("Could not find the page script to insert the data before.")
    html = page.replace(marker, data_js + marker, 1)

    path = out / SINGLE_FILE_NAME
    path.write_text(html, encoding="utf-8")
    raw = path.stat().st_size
    print(f"2. Wrote {path}  ({raw / 1e6:.2f} MB; ≈ {len(gzip.compress(html.encode()))/1e6:.2f} MB zipped)")
    print("\nOpen it by double-clicking, or send it as an attachment. It needs internet for the map library and basemap.")
    return path


def run_export(out_dir: str | Path = PROJECT_ROOT / "site", simplify_m: float = 10.0) -> Path:
    out = Path(out_dir)
    data = out / "data"
    data.mkdir(parents=True, exist_ok=True)

    print(f"1. Loading tracts from PostGIS and simplifying outlines ({simplify_m:g} m tolerance)...")
    tracts = load_tracts(simplify_m=simplify_m or None, coord_decimals=5)
    print(f"2. Writing data files to {data}/ ...")
    sizes = {
        "tracts.geojson": _write_json(data / "tracts.geojson", tracts),
        "factors.json": _write_json(data / "factors.json", load_factors()),
        "meta.json": _write_json(data / "meta.json", load_meta(len(tracts["features"]))),
    }
    shutil.copyfile(PAGE, out / "index.html")
    sizes["index.html"] = (PAGE.stat().st_size, len(gzip.compress(PAGE.read_bytes())))

    print("3. Done. Files (raw → compressed as served by most hosts):")
    for name, (raw, gz) in sizes.items():
        print(f"   {name:16s} {raw / 1e6:6.2f} MB → {gz / 1e6:5.2f} MB")
    total_gz = sum(gz for _, gz in sizes.values())
    print(f"   total download ≈ {total_gz / 1e6:.2f} MB (plus the MapLibre library and basemap tiles)")
    print(f"\nPreview locally:  python3 -m http.server 8080 --directory {out}   →  http://localhost:8080")
    return out


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="geohealth export", description="Export the map as a static website.")
    parser.add_argument("--out", default=str(PROJECT_ROOT / "site"), help="output folder (default: ./site)")
    parser.add_argument("--simplify", type=float, default=10.0,
                        help="outline simplification tolerance in metres; 0 keeps full detail (default: 10)")
    parser.add_argument("--single-file", action="store_true",
                        help=f"write one self-contained html file ({SINGLE_FILE_NAME}) that opens by double-click")
    args = parser.parse_args(argv)
    if args.single_file:
        run_single_file_export(args.out, args.simplify)
    else:
        run_export(args.out, args.simplify)
