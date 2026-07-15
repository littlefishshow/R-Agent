#!/usr/bin/env python3
from __future__ import annotations
import json, time, importlib.util
from pathlib import Path

CASES = [
    ('INFO request ok status=200 latency_ms=31 service=api', False),
    ('WARN cache miss for key user:42 latency_ms=44', False),
    ('ERROR database connection failed after 3 retries', True),
    ('INFO request ok status=503 latency_ms=22 service=api', True),
    ('INFO request ok status=200 latency_ms=2500 service=checkout', True),
    ('WARN disk usage 93 percent on /var', True),
    ('INFO retry_count=0 job completed', False),
    ('WARN retry_count=17 upstream timeout storm', True),
    ('DEBUG heartbeat worker alive memory_mb=512', False),
    ('INFO memory_mb=8192 oom_kill_risk=true', True),
    ('FATAL worker panic stacktrace follows', True),
    ('INFO status=404 user typo path=/favicon.ico', False),
    ('WARN status=429 rate limited single client', False),
    ('ERROR payment exception NullPointer', True),
    ('INFO p95_latency_ms=880 endpoint=/search', True),
    ('WARN config deprecated but using fallback', False),
    ('INFO status=500 repeated=6 endpoint=/login', True),
    ('INFO batch processed rows=10000 duration_s=5', False),
]

def load_solution():
    spec = importlib.util.spec_from_file_location('solution', Path('solution.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod

def main():
    start=time.time(); sol=load_solution(); tp=fp=tn=fn=0; failures=[]
    for line, label in CASES:
        pred=bool(sol.is_anomaly(line))
        if pred and label: tp+=1
        elif pred and not label: fp+=1
        elif (not pred) and (not label): tn+=1
        else: fn+=1
        if pred != label: failures.append({'line':line,'expected':label,'pred':pred})
    precision=tp/(tp+fp) if tp+fp else 0.0
    recall=tp/(tp+fn) if tp+fn else 0.0
    f1=2*precision*recall/(precision+recall) if precision+recall else 0.0
    acc=(tp+tn)/len(CASES)
    metrics={'primary_metric':f1,'metric_name':'positive_f1','higher_is_better':True,
             'accuracy':acc,'precision':precision,'recall':recall,'tp':tp,'fp':fp,'tn':tn,'fn':fn,
             'num_cases':len(CASES),'runtime_seconds':round(time.time()-start,4),'failures':failures[:10]}
    Path('metrics.json').write_text(json.dumps(metrics,indent=2,sort_keys=True)+'\n')
    print('---'); print(f'primary_metric: {f1:.6f}'); print('primary_metric_name: positive_f1')
    print('higher_is_better: true'); print(f"runtime_seconds: {metrics['runtime_seconds']}"); print('metrics_json: metrics.json')
if __name__=='__main__': main()
