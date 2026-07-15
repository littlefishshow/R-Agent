#!/usr/bin/env python3
from __future__ import annotations
import json, time, importlib.util
from pathlib import Path

CASES = [
    ("  ACME   Widget XL!! ", "acme widget extra large"),
    ("acme widgit xl", "acme widget extra large"),
    ("ACME widget extra-large", "acme widget extra large"),
    ("Mega Phone Pro v2", "mega phone pro version 2"),
    ("mega-phone profesional version II", "mega phone pro version 2"),
    ("budget USB C cabel", "budget usb c cable"),
    ("Budget usb-c cable", "budget usb c cable"),
    ("Noise Cancelling Headphones", "noise canceling headphones"),
    ("noise-canceling headphone", "noise canceling headphones"),
    ("4k ultra hd monitor", "4k ultra hd monitor"),
    ("Ultra-HD 4K monitor", "4k ultra hd monitor"),
    ("stainless steel water-bottle", "stainless steel water bottle"),
    ("stainles steel water bottle", "stainless steel water bottle"),
    ("kids t shirt blue", "kids t shirt blue"),
    ("kid's tee-shirt, blue", "kids t shirt blue"),
    ("laptop sleeve thirteen inch", "laptop sleeve 13 inch"),
    ("Laptop Sleeve 13\"", "laptop sleeve 13 inch"),
    ("wireless mouse ergonomic", "wireless ergonomic mouse"),
]

def load_solution():
    spec = importlib.util.spec_from_file_location('solution', Path('solution.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod

def main():
    start = time.time()
    sol = load_solution()
    rows = []
    correct = 0
    for src, expected in CASES:
        pred = sol.normalize(src)
        ok = pred == expected
        correct += int(ok)
        rows.append({'input': src, 'expected': expected, 'pred': pred, 'ok': ok})
    acc = correct / len(CASES)
    metrics = {
        'primary_metric': acc,
        'metric_name': 'exact_match_accuracy',
        'higher_is_better': True,
        'num_cases': len(CASES),
        'num_correct': correct,
        'runtime_seconds': round(time.time() - start, 4),
        'failures': [r for r in rows if not r['ok']][:10],
    }
    Path('metrics.json').write_text(json.dumps(metrics, indent=2, sort_keys=True) + '\n')
    print('---')
    print(f"primary_metric: {acc:.6f}")
    print('primary_metric_name: exact_match_accuracy')
    print('higher_is_better: true')
    print(f"runtime_seconds: {metrics['runtime_seconds']}")
    print('metrics_json: metrics.json')

if __name__ == '__main__':
    main()
