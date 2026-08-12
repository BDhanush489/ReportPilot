"""
Cross-checks every visual.json field reference (Entity.Property) against the
actual columns/measures declared in the TMDL table files, and checks
relationship from/toColumn references too. jsonschema validation (validate_pbip.py)
proves the JSON shape is right; this proves the *content* points at real fields.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent
TABLES_DIR = ROOT / "AuroraHomeGoods.SemanticModel" / "definition" / "tables"

def parse_table_members(tmdl_text: str) -> set[str]:
    members = set()
    for m in re.finditer(r"^\t(?:column|measure) (?:'([^']+)'|(\S+))", tmdl_text, re.MULTILINE):
        members.add(m.group(1) or m.group(2))
    return members

model = {}
for f in TABLES_DIR.glob("*.tmdl"):
    table_name = f.stem
    model[table_name] = parse_table_members(f.read_text(encoding="utf-8"))

print("Tables discovered:", {k: len(v) for k, v in model.items()})

errors = []

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

visual_files = sorted((ROOT / "AuroraHomeGoods.Report" / "definition" / "pages").glob("*/visuals/*/visual.json"))
for vf in visual_files:
    doc = json.loads(vf.read_text(encoding="utf-8"))
    walk_fields(doc, str(vf.relative_to(ROOT)))

# relationships
rel_text = (ROOT / "AuroraHomeGoods.SemanticModel" / "definition" / "relationships.tmdl").read_text(encoding="utf-8")
for m in re.finditer(r"fromColumn: (\S+)\.(\S+)\s*\n\ttoColumn: (\S+)\.(\S+)", rel_text):
    ft, fc, tt, tc = m.groups()
    if ft not in model or fc not in model[ft]:
        errors.append(f"relationship fromColumn {ft}.{fc} invalid")
    if tt not in model or tc not in model[tt]:
        errors.append(f"relationship toColumn {tt}.{tc} invalid")

if errors:
    print(f"\n{len(errors)} FIELD REFERENCE ERROR(S):")
    for e in errors:
        print(" -", e)
else:
    print(f"\nAll field references across {len(visual_files)} visuals + relationships resolve correctly.")
