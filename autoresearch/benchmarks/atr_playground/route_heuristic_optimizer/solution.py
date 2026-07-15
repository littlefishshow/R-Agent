import math

def solve(points):
    """Baseline nearest-neighbor tour starting at 0. Return a permutation of point indices."""
    n = len(points)
    if n == 0:
        return []
    unvisited = set(range(1, n))
    tour = [0]
    cur = 0
    while unvisited:
        nx = min(unvisited, key=lambda j: (points[cur][0]-points[j][0])**2 + (points[cur][1]-points[j][1])**2)
        unvisited.remove(nx)
        tour.append(nx)
        cur = nx
    return tour
