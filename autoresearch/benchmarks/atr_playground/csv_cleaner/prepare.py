import csv, json
from pathlib import Path
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

def main():
    DATA.mkdir(exist_ok=True)
    states = [("CA", "California"), ("NY", "New York"), ("TX", "Texas"), ("WA", "Washington"), ("MA", "Massachusetts")]
    base_names = ["Alice Smith", "Bob Jones", "Carol O'Neil", "Dan Brown", "Eve Stone", "Frank Miller", "Grace Lee", "Hank Green"]
    rows = []
    truth = {}
    for i in range(80):
        name = base_names[i % len(base_names)]
        age = 18 + (i * 7) % 55
        code, full = states[i % len(states)]
        email = name.lower().replace("'", "").replace(" ", ".") + f"{i}@example.com"
        dirty_name = name
        if i % 2 == 0: dirty_name = "  " + dirty_name.upper() + "  "
        if i % 3 == 0: dirty_name = dirty_name.replace("'", "")
        if i % 5 == 0: dirty_name = dirty_name.replace(" ", "  ")
        dirty_age = str(age) if i % 4 else f"{age} years"
        if i % 9 == 0: dirty_age = "unknown"
        clean_age = "" if dirty_age == "unknown" else str(age)
        dirty_email = email
        if i % 3 == 1: dirty_email = dirty_email.replace("@", " [at] ")
        if i % 4 == 2: dirty_email = " " + dirty_email.upper() + " "
        if i % 7 == 0: dirty_email = dirty_email.replace(".", " ", 1)
        dirty_state = code if i % 3 == 0 else full
        if i % 4 == 0: dirty_state = dirty_state.lower()
        rid = f"row_{i:03d}"
        rows.append({"id": rid, "name": dirty_name, "age": dirty_age, "email": dirty_email, "state": dirty_state})
        truth[rid] = {"name": " ".join(name.replace("'", "").split()).title(), "age": clean_age, "email": email, "state": code}
    with (DATA / "dirty.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "name", "age", "email", "state"])
        writer.writeheader(); writer.writerows(rows)
    (DATA / "truth.json").write_text(json.dumps(truth, indent=2), encoding="utf-8")
    print(f"wrote {len(rows)} rows")

if __name__ == "__main__":
    main()
