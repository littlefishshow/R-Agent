import csv, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import prepare
if not (ROOT / "data" / "dirty.csv").exists():
    prepare.main()
SUB = ROOT / "submission"; SUB.mkdir(exist_ok=True)
cleaner_code = '''
def clean_row(row):
    # Incomplete baseline: strips fields and lowercases email only.
    return {
        "name": row.get("name", "").strip(),
        "age": row.get("age", "").strip(),
        "email": row.get("email", "").strip().lower(),
        "state": row.get("state", "").strip().upper(),
    }
'''
(SUB / "cleaner.py").write_text(cleaner_code, encoding="utf-8")
ns = {}; exec(cleaner_code, ns)
with (ROOT / "data" / "dirty.csv").open(newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))
preds = {r["id"]: ns["clean_row"]({k: v for k, v in r.items() if k != "id"}) for r in rows}
(SUB / "predictions.json").write_text(json.dumps(preds, indent=2), encoding="utf-8")
print(f"wrote {len(preds)} predictions")
