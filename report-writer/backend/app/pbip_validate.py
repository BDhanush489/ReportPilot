"""
Test-gate validation for pbip_export.py's output, parameterized from the
hand-built d:\\IMDollars\\powerbi\\validate_pbip.py (JSON Schema validation
against Microsoft's published schemas) and check_field_references.py
(every visual field binding resolves to a real model column/measure) --
same two checks, now a function of any project directory instead of a
script hardcoded to AuroraHomeGoods.*, so they gate build_pbip()'s output
for any client as pytest tests, not a manually-run script.

Honest scope note: `.platform` and `definition.pbism` carry a `$schema`
field but (per the PBIP format's own convention, unchanged here) aren't
named `*.json` -- neither the original script nor this one match them.
Schema validation only ever finds something to check once a `*.Report/`
folder exists (D2.1) with its `page.json`/`visual.json`/etc. files; a
D2.0-only SemanticModel genuinely has zero `*.json` files to validate,
and this reports that as `checked == 0`, not a fabricated pass.
"""
from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA_CACHE_DIR = Path(__file__).resolve().parent.parent / ".pbip_schema_cache"


def fetch_schema(url: str) -> dict:
    SCHEMA_CACHE_DIR.mkdir(exist_ok=True)
    cache_file = SCHEMA_CACHE_DIR / (url.replace("https://", "").replace("/", "_") + ".json")
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))
    req = urllib.request.Request(url, headers={"User-Agent": "pbip-validator/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = resp.read().decode("utf-8")
    cache_file.write_text(data, encoding="utf-8")
    return json.loads(data)


@dataclass
class SchemaValidationResult:
    checked: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def validate_schemas(project_dir: Path) -> SchemaValidationResult:
    import jsonschema

    result = SchemaValidationResult()
    for f in sorted(Path(project_dir).glob("**/*.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            result.failures.append(f"[SYNTAX ERROR] {f}: {exc}")
            continue
        schema_url = doc.get("$schema")
        if not schema_url:
            continue
        try:
            schema = fetch_schema(schema_url)
            jsonschema.validate(instance=doc, schema=schema)
            result.checked += 1
        except jsonschema.exceptions.ValidationError as exc:
            result.failures.append(f"[SCHEMA FAIL] {f} at {list(exc.absolute_path)}: {exc.message}")
        except Exception as exc:  # noqa: BLE001
            result.failures.append(f"[FETCH/OTHER ERROR] {f}: {exc}")
    return result


def parse_table_members(tmdl_text: str) -> set[str]:
    members = set()
    for m in re.finditer(r"^\t(?:column|measure) (?:'([^']+)'|(\S+))", tmdl_text, re.MULTILINE):
        members.add(m.group(1) or m.group(2))
    return members


def load_model(project_dir: Path) -> dict[str, set[str]]:
    model: dict[str, set[str]] = {}
    for tables_dir in Path(project_dir).glob("*.SemanticModel/definition/tables"):
        for f in tables_dir.glob("*.tmdl"):
            model[f.stem] = parse_table_members(f.read_text(encoding="utf-8"))
    return model


def check_field_references(project_dir: Path) -> list[str]:
    """Cross-checks every *.Report visual.json field reference against the
    real columns/measures declared in *.SemanticModel's TMDL tables, plus
    every relationship's fromColumn/toColumn. Empty list = every reference
    resolves; a D2.0-only project (no Report/ yet, no relationships.tmdl
    yet) legitimately returns an empty list too -- nothing to check is not
    the same claim as "everything checked out", but it's not a failure."""
    model = load_model(project_dir)
    errors: list[str] = []

    def walk_fields(obj, path):
        if isinstance(obj, dict):
            if "Column" in obj or "Measure" in obj:
                kind = "Column" if "Column" in obj else "Measure"
                inner = obj[kind]
                entity = inner["Expression"]["SourceRef"]["Entity"]
                prop = inner["Property"]
                if entity not in model:
                    errors.append(f"{path}: unknown table '{entity}'")
                elif prop not in model[entity]:
                    errors.append(f"{path}: '{entity}.{prop}' not found (available: {sorted(model[entity])})")
            for k, v in obj.items():
                walk_fields(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk_fields(v, f"{path}[{i}]")

    for vf in sorted(Path(project_dir).glob("*.Report/definition/pages/*/visuals/*/visual.json")):
        doc = json.loads(vf.read_text(encoding="utf-8"))
        walk_fields(doc, str(vf))

    for rel_file in Path(project_dir).glob("*.SemanticModel/definition/relationships.tmdl"):
        rel_text = rel_file.read_text(encoding="utf-8")
        for m in re.finditer(r"fromColumn: (\S+)\.(\S+)\s*\n\ttoColumn: (\S+)\.(\S+)", rel_text):
            ft, fc, tt, tc = m.groups()
            if ft not in model or fc not in model[ft]:
                errors.append(f"relationship fromColumn {ft}.{fc} invalid")
            if tt not in model or tc not in model[tt]:
                errors.append(f"relationship toColumn {tt}.{tc} invalid")

    return errors
