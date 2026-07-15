import json, random
from pathlib import Path
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

def main():
    DATA.mkdir(exist_ok=True)
    rng = random.Random(123)
    alphabet = "abcd"
    patterns = ["ab", "bc", "abc", "bcd", "aa", "dad", "abcd", "cdab", "aab", "ddc"]
    cases = []
    for i in range(16):
        text = ''.join(rng.choice(alphabet) for _ in range(2500 + i * 200))
        text += ("abcddcdaab" * (20 + i))
        cases.append({"id": f"sm_{i:03d}", "text": text, "patterns": patterns})
    (DATA / "test_cases.json").write_text(json.dumps(cases, indent=2), encoding="utf-8")
    print(f"wrote {len(cases)} cases")

if __name__ == "__main__":
    main()
