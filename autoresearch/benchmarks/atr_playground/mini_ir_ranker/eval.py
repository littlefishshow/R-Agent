#!/usr/bin/env python3
from __future__ import annotations
import json, time, importlib.util
from pathlib import Path

DOCS = [
    "How to fix a bicycle flat tire using patch glue and tire levers",
    "Python list comprehensions and dictionary iteration examples",
    "Guide to brewing espresso with fine grind size and pressure",
    "Troubleshooting laptop battery drain during sleep mode",
    "Neural network regularization with dropout and weight decay",
    "Best practices for tomato seedling watering and sunlight",
    "USB C cable charging wattage and data transfer speed explained",
    "Symptoms of seasonal allergies: sneezing, itchy eyes, pollen",
    "How to reset a router and improve home WiFi signal",
    "Beginner strength training plan with squats and deadlifts",
]
QUERIES = [
    ("repair punctured bike tube", 0),
    ("python dict loop compact syntax", 1),
    ("make strong coffee with espresso machine", 2),
    ("notebook loses power while sleeping", 3),
    ("avoid overfitting in neural nets", 4),
    ("watering young tomato plants", 5),
    ("type c cord fast charging", 6),
    ("pollen makes eyes itch and sneeze", 7),
    ("wifi router restart weak signal", 8),
    ("gym routine squat deadlift beginner", 9),
    ("battery drain sleep laptop", 3),
    ("dropout weight decay model", 4),
]

def load_solution():
    spec = importlib.util.spec_from_file_location('solution', Path('solution.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod

def main():
    start = time.time(); sol = load_solution()
    reciprocal = []; top1 = 0; failures = []
    for q, target in QUERIES:
        order = sol.rank(q, DOCS)
        pos = order.index(target) + 1 if target in order else len(DOCS) + 1
        reciprocal.append(1 / pos)
        top1 += int(pos == 1)
        if pos != 1:
            failures.append({'query': q, 'target': target, 'rank': pos, 'top3': order[:3]})
    mrr = sum(reciprocal) / len(reciprocal)
    metrics = {'primary_metric': mrr, 'metric_name': 'mean_reciprocal_rank', 'higher_is_better': True,
               'top1_accuracy': top1 / len(QUERIES), 'num_queries': len(QUERIES),
               'runtime_seconds': round(time.time() - start, 4), 'failures': failures[:10]}
    Path('metrics.json').write_text(json.dumps(metrics, indent=2, sort_keys=True) + '\n')
    print('---')
    print(f'primary_metric: {mrr:.6f}')
    print('primary_metric_name: mean_reciprocal_rank')
    print('higher_is_better: true')
    print(f"runtime_seconds: {metrics['runtime_seconds']}")
    print('metrics_json: metrics.json')
if __name__ == '__main__': main()
