import json, random
from pathlib import Path
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

def main():
    DATA.mkdir(exist_ok=True)
    rng = random.Random(456)
    cases = [
        {"id":"ks_000", "capacity":50, "items":[{"weight":10,"value":60},{"weight":20,"value":100},{"weight":30,"value":120}]},
        {"id":"ks_001", "capacity":10, "items":[{"weight":6,"value":30},{"weight":3,"value":14},{"weight":4,"value":16},{"weight":2,"value":9}]},
        {"id":"ks_002", "capacity":7, "items":[{"weight":5,"value":10},{"weight":4,"value":40},{"weight":3,"value":30},{"weight":2,"value":50}]},
    ]
    for i in range(3, 43):
        n = 12 + (i % 8)
        capacity = 35 + (i * 7) % 80
        items = []
        for _ in range(n):
            items.append({"weight": rng.randint(2, 30), "value": rng.randint(5, 120)})
        items.append({"weight": capacity // 2 + 1, "value": capacity * 2})
        items.append({"weight": capacity // 2, "value": capacity + 20})
        items.append({"weight": capacity // 2, "value": capacity + 20})
        cases.append({"id": f"ks_{i:03d}", "capacity": capacity, "items": items})
    (DATA / "test_cases.json").write_text(json.dumps(cases, indent=2), encoding="utf-8")
    print(f"wrote {len(cases)} cases")

if __name__ == "__main__":
    main()
