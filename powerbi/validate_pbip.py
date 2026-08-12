"""
Validates every generated PBIR/TMDL-adjacent JSON file against its own declared
$schema (fetched from developer.microsoft.com and cached locally). This is the
closest available check to Power BI Desktop's own validation without Desktop
installed — it catches structural/schema mistakes, not TMDL syntax errors.
"""
import json
import sys
import urllib.request
from pathlib import Path

import jsonschema

ROOT = Path(__file__).parent
CACHE = ROOT / ".schema_cache"
CACHE.mkdir(exist_ok=True)


def fetch_schema(url: str) -> dict:
    cache_file = CACHE / (url.replace("https://", "").replace("/", "_") + ".json")
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))
    req = urllib.request.Request(url, headers={"User-Agent": "pbip-validator/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = resp.read().decode("utf-8")
    cache_file.write_text(data, encoding="utf-8")
    return json.loads(data)


def main():
    json_files = sorted(ROOT.glob("AuroraHomeGoods.*/**/*.json"))
    failures = 0
    checked = 0

    for f in json_files:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"[SYNTAX ERROR] {f.relative_to(ROOT)}: {e}")
            failures += 1
            continue

        schema_url = doc.get("$schema")
        if not schema_url:
            print(f"[SKIP no $schema] {f.relative_to(ROOT)}")
            continue

        try:
            schema = fetch_schema(schema_url)
            jsonschema.validate(instance=doc, schema=schema)
            checked += 1
        except jsonschema.exceptions.ValidationError as e:
            print(f"[SCHEMA FAIL] {f.relative_to(ROOT)}")
            print(f"    at {list(e.absolute_path)}: {e.message}")
            failures += 1
        except Exception as e:  # noqa: BLE001
            print(f"[FETCH/OTHER ERROR] {f.relative_to(ROOT)}: {e}")
            failures += 1

    print(f"\n{checked} file(s) passed schema validation, {failures} failure(s), "
          f"out of {len(json_files)} JSON files found.")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
