
def clean_row(row):
    # Incomplete baseline: strips fields and lowercases email only.
    return {
        "name": row.get("name", "").strip(),
        "age": row.get("age", "").strip(),
        "email": row.get("email", "").strip().lower(),
        "state": row.get("state", "").strip().upper(),
    }
