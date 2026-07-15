
def count_pattern(text, pat):
    n, m = len(text), len(pat)
    total = 0
    for i in range(0, n - m + 1):
        if text[i:i+m] == pat:
            total += 1
    return total

def solve_case(text, patterns):
    return {p: count_pattern(text, p) for p in patterns}
