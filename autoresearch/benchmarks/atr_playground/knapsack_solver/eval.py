import importlib.util, json, time, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
if not (ROOT / "data" / "test_cases.json").exists():
    subprocess.check_call([sys.executable, "prepare.py"], cwd=ROOT)

def optimal(capacity, items):
    dp = [0] * (capacity + 1)
    for it in items:
        w, v = int(it["weight"]), int(it["value"])
        for c in range(capacity, w - 1, -1):
            cand = dp[c-w] + v
            if cand > dp[c]: dp[c] = cand
    return max(dp)
cases = json.loads((ROOT / "data" / "test_cases.json").read_text())
expected = {c["id"]: optimal(c["capacity"], c["items"]) for c in cases}
pred_path = ROOT / "submission" / "predictions.json"
preds = json.loads(pred_path.read_text()) if pred_path.exists() else {}
correct = sum(1 for k, v in expected.items() if preds.get(k) == v)
exact_accuracy = correct / len(cases)
value_ratio = sum(min(float(preds.get(k, 0)) / v, 1.0) if v else 1.0 for k, v in expected.items()) / len(cases)
runtime_sec = None
sp = ROOT / "submission" / "solver.py"
if sp.exists():
    spec = importlib.util.spec_from_file_location("submitted_knapsack", sp)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    t0 = time.perf_counter(); ok = 0
    for _ in range(20):
        for c in cases:
            ok += (mod.solve_case(c["capacity"], c["items"]) == expected[c["id"]])
    runtime_sec = time.perf_counter() - t0
    exact_accuracy = min(exact_accuracy, ok / (20 * len(cases)))
score = 0.75 * exact_accuracy + 0.25 * value_ratio
metrics = {"primary_metric": score, "primary_metric_name": "score", "higher_is_better": True,
           "exact_accuracy": exact_accuracy, "correct": correct, "total": len(cases),
           "value_ratio": value_ratio, "runtime_sec": runtime_sec, "score": score}
(ROOT / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
print(json.dumps(metrics, indent=2))
