import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import prepare
if not (ROOT / "data" / "test_cases.json").exists():
    prepare.main()
SUB = ROOT / "submission"; SUB.mkdir(exist_ok=True)
solver_code = '''
def solve_case(capacity, items):
    remaining = int(capacity)
    total = 0
    ordered = sorted(items, key=lambda x: (x["value"] / x["weight"], x["value"]), reverse=True)
    for it in ordered:
        w, v = int(it["weight"]), int(it["value"])
        if w <= remaining:
            remaining -= w
            total += v
    return total
'''
(SUB / "solver.py").write_text(solver_code, encoding="utf-8")
ns = {}; exec(solver_code, ns)
cases = json.loads((ROOT / "data" / "test_cases.json").read_text())
preds = {c["id"]: ns["solve_case"](c["capacity"], c["items"]) for c in cases}
(SUB / "predictions.json").write_text(json.dumps(preds, indent=2), encoding="utf-8")
print(f"wrote {len(preds)} predictions")
