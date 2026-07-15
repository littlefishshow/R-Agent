import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import prepare
if not (ROOT / "data" / "test_cases.json").exists():
    prepare.main()
SUB = ROOT / "submission"; SUB.mkdir(exist_ok=True)
matcher_code = '''
def count_pattern(text, pat):
    n, m = len(text), len(pat)
    total = 0
    for i in range(0, n - m + 1):
        if text[i:i+m] == pat:
            total += 1
    return total

def solve_case(text, patterns):
    return {p: count_pattern(text, p) for p in patterns}
'''
(SUB / "matcher.py").write_text(matcher_code, encoding="utf-8")
ns = {}; exec(matcher_code, ns)
cases = json.loads((ROOT / "data" / "test_cases.json").read_text())
preds = {c["id"]: ns["solve_case"](c["text"], c["patterns"]) for c in cases}
(SUB / "predictions.json").write_text(json.dumps(preds, indent=2), encoding="utf-8")
print(f"wrote {len(preds)} predictions")
