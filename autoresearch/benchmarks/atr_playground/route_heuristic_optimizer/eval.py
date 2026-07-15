#!/usr/bin/env python3
from __future__ import annotations
import json, math, itertools, time, importlib.util
from pathlib import Path

INSTANCES = [
    [(0,0),(1,0),(1,1),(0,1),(0.5,0.5),(2,0),(2,1)],
    [(0,0),(2,0),(4,0),(1,1),(3,1),(0,3),(2,3),(4,3)],
    [(0,0),(1,3),(2,1),(3,4),(4,0),(5,3),(6,1),(2,5)],
    [(0,0),(0,2),(0,4),(2,0),(2,2),(2,4),(4,0),(4,2),(4,4)],
]

def dist(a,b): return math.hypot(a[0]-b[0], a[1]-b[1])
def length(points, tour):
    return sum(dist(points[tour[i]], points[tour[(i+1)%len(tour)]]) for i in range(len(tour)))
def exact_best(points):
    n=len(points); best=1e9
    for perm in itertools.permutations(range(1,n)):
        tour=(0,)+perm
        best=min(best, length(points,tour))
    return best
BEST=[exact_best(p) for p in INSTANCES]

def load_solution():
    spec = importlib.util.spec_from_file_location('solution', Path('solution.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod

def main():
    start=time.time(); sol=load_solution(); scores=[]; rows=[]
    for idx, pts in enumerate(INSTANCES):
        tour=sol.solve([tuple(p) for p in pts])
        valid=sorted(tour)==list(range(len(pts)))
        if not valid:
            produced=1e9; score=0.0
        else:
            produced=length(pts, tour); score=min(1.0, BEST[idx]/produced)
        scores.append(score); rows.append({'instance':idx,'valid':valid,'length':produced,'best':BEST[idx],'score':score,'tour':tour})
    primary=sum(scores)/len(scores)
    metrics={'primary_metric':primary,'metric_name':'route_quality_score','higher_is_better':True,
             'num_instances':len(INSTANCES),'runtime_seconds':round(time.time()-start,4),'instances':rows}
    Path('metrics.json').write_text(json.dumps(metrics,indent=2,sort_keys=True)+'\n')
    print('---'); print(f'primary_metric: {primary:.6f}'); print('primary_metric_name: route_quality_score')
    print('higher_is_better: true'); print(f"runtime_seconds: {metrics['runtime_seconds']}"); print('metrics_json: metrics.json')
if __name__=='__main__': main()
