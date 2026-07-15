import csv, importlib.util, json, subprocess, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent
if not (ROOT / "data" / "dirty.csv").exists():
    subprocess.check_call([sys.executable, "prepare.py"], cwd=ROOT)
truth = json.loads((ROOT / "data" / "truth.json").read_text())
pred_path = ROOT / "submission" / "predictions.json"
preds = json.loads(pred_path.read_text()) if pred_path.exists() else {}
fields = ["name", "age", "email", "state"]
cell_total = len(truth) * len(fields)
cell_correct = 0; row_correct = 0
for rid, exp in truth.items():
    pr = preds.get(rid, {})
    row_ok = True
    for f in fields:
        ok = pr.get(f, None) == exp[f]
        cell_correct += int(ok); row_ok = row_ok and ok
    row_correct += int(row_ok)
cell_accuracy = cell_correct / cell_total
row_accuracy = row_correct / len(truth)
cell_f1 = cell_accuracy
runtime_sec = None
cp = ROOT / "submission" / "cleaner.py"
if cp.exists():
    spec = importlib.util.spec_from_file_location("submitted_cleaner", cp)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    with (ROOT / "data" / "dirty.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    t0 = time.perf_counter()
    for _ in range(100):
        for r in rows:
            mod.clean_row({k: v for k, v in r.items() if k != "id"})
    runtime_sec = time.perf_counter() - t0
score = 0.65 * row_accuracy + 0.35 * cell_f1
metrics = {"primary_metric": score, "primary_metric_name": "score", "higher_is_better": True,
           "row_accuracy": row_accuracy, "cell_accuracy": cell_accuracy, "cell_f1": cell_f1,
           "row_correct": row_correct, "total_rows": len(truth), "runtime_sec": runtime_sec, "score": score}
(ROOT / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
print(json.dumps(metrics, indent=2))
