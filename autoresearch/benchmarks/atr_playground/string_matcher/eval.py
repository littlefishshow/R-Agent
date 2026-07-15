import importlib.util, json, time, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
if not (ROOT / "data" / "test_cases.json").exists():
    subprocess.check_call([sys.executable, "prepare.py"], cwd=ROOT)

def expected_counts(text, patterns):
    out = {}
    for p in patterns:
        start = 0; cnt = 0
        while True:
            j = text.find(p, start)
            if j < 0: break
            cnt += 1; start = j + 1
        out[p] = cnt
    return out
cases = json.loads((ROOT / "data" / "test_cases.json").read_text())
expected = {c["id"]: expected_counts(c["text"], c["patterns"]) for c in cases}
pred_path = ROOT / "submission" / "predictions.json"
preds = json.loads(pred_path.read_text()) if pred_path.exists() else {}
correct = sum(1 for k, v in expected.items() if preds.get(k) == v)
accuracy = correct / len(cases)
runtime_sec = None
mp = ROOT / "submission" / "matcher.py"
if mp.exists():
    spec = importlib.util.spec_from_file_location("submitted_matcher", mp)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    t0 = time.perf_counter(); ok = 0
    for _ in range(10):
        for c in cases:
            ok += (mod.solve_case(c["text"], c["patterns"]) == expected[c["id"]])
    runtime_sec = time.perf_counter() - t0
    if ok != 10 * len(cases): accuracy = min(accuracy, ok / (10 * len(cases)))
speed_score = 1.0 if runtime_sec is None else min(1.0, 0.30 / max(runtime_sec, 1e-9))
score = accuracy * (0.80 + 0.20 * speed_score)
metrics = {"primary_metric": score, "primary_metric_name": "score", "higher_is_better": True,
           "accuracy": accuracy, "correct": correct, "total": len(cases), "runtime_sec": runtime_sec,
           "speed_score": speed_score, "score": score}
(ROOT / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
print(json.dumps(metrics, indent=2))
