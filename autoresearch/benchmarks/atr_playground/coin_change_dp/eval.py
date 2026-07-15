import importlib.util, json, time, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if not (ROOT / "data" / "test_cases.json").exists():
    subprocess.check_call([sys.executable, "prepare.py"], cwd=ROOT)

def optimal(coins, amount):
    if amount < 0:
        return -1
    inf = 10**9
    dp = [0] + [inf] * amount
    for a in range(1, amount + 1):
        best = inf
        for c in coins:
            if c <= a and dp[a-c] + 1 < best:
                best = dp[a-c] + 1
        dp[a] = best
    return -1 if dp[amount] >= inf else dp[amount]

cases = json.loads((ROOT / "data" / "test_cases.json").read_text())
expected = {c["id"]: optimal(c["coins"], c["amount"]) for c in cases}
pred_path = ROOT / "submission" / "predictions.json"
preds = json.loads(pred_path.read_text()) if pred_path.exists() else {}
correct = sum(1 for k, v in expected.items() if preds.get(k) == v)
accuracy = correct / len(cases)
solver_path = ROOT / "submission" / "solver.py"
runtime_sec = None
if solver_path.exists():
    spec = importlib.util.spec_from_file_location("submitted_solver", solver_path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    t0 = time.perf_counter(); ok = 0
    for _ in range(20):
        for c in cases:
            ok += (mod.solve_case(c["coins"], c["amount"]) == expected[c["id"]])
    runtime_sec = time.perf_counter() - t0
    if ok != 20 * len(cases):
        accuracy = min(accuracy, ok / (20 * len(cases)))
speed_score = 1.0 if runtime_sec is None else min(1.0, 0.50 / max(runtime_sec, 1e-9))
score = accuracy * (0.85 + 0.15 * speed_score)
metrics = {"primary_metric": score, "primary_metric_name": "score", "higher_is_better": True,
           "accuracy": accuracy, "correct": correct, "total": len(cases), "runtime_sec": runtime_sec,
           "speed_score": speed_score, "score": score}
(ROOT / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
print(json.dumps(metrics, indent=2))
