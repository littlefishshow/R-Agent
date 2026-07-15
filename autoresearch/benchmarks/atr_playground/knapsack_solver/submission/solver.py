
def solve_case(capacity, items):
    remaining = int(capacity)
    total = 0
    ordered = sorted(items, key=lambda x: (x["value"] / x["weight"], x["value"]), reverse=True)
    for it in ordered:
        w, v = int(it["weight"]), int(it["value"])
        if w <= remaining:
            remaining -= w
            total += v
    return total
