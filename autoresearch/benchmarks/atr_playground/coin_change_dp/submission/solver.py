
import sys
from functools import lru_cache

def solve_case(coins, amount):
    sys.setrecursionlimit(max(10000, int(amount) + 100))
    coins = tuple(sorted(set(int(c) for c in coins if int(c) > 0), reverse=True))
    if amount < 0:
        return -1
    @lru_cache(None)
    def rec(rem):
        if rem == 0:
            return 0
        best = 10**9
        for c in coins:
            if c <= rem:
                sub = rec(rem - c)
                if sub + 1 < best:
                    best = sub + 1
        return best
    ans = rec(int(amount))
    return -1 if ans >= 10**9 else ans
