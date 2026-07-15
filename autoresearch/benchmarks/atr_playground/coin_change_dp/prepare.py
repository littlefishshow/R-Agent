import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

def main():
    DATA.mkdir(exist_ok=True)
    cases = []
    coin_sets = [
        [1, 3, 4], [1, 5, 10, 25], [2, 7, 13], [3, 6, 9, 20], [4, 11, 17],
        [1, 7, 23, 32], [5, 9, 18, 40], [6, 10, 15, 25], [1, 11, 37, 99]
    ]
    idx = 0
    for coins in coin_sets:
        for amount in [0, 1, 2, 6, 17, 31, 63, 127, 255, 511, 999, 1500, 2500]:
            cases.append({"id": f"cc_{idx:04d}", "coins": coins, "amount": amount})
            idx += 1
    for amount in range(100, 2100, 100):
        cases.append({"id": f"cc_{idx:04d}", "coins": [1, 7, 10, 22, 57], "amount": amount})
        idx += 1
    (DATA / "test_cases.json").write_text(json.dumps(cases, indent=2), encoding="utf-8")
    print(f"wrote {len(cases)} cases to {DATA/'test_cases.json'}")

if __name__ == "__main__":
    main()
