#!/usr/bin/env python3
from __future__ import annotations
import json, time, importlib.util
from pathlib import Path

CASES = [
    ("{'a': 1, 'b': 2}", {"a":1,"b":2}),
    ('{"a":1, "b":2,}', {"a":1,"b":2}),
    ('{name: "alice", age: 30}', {"name":"alice","age":30}),
    ("{'ok': True, 'value': None}", {"ok":True,"value":None}),
    ('[1,2,3,]', [1,2,3]),
    ('{"items": ["a", "b",], "n": 2}', {"items":["a","b"],"n":2}),
    ('// comment\n{"x": 5}', {"x":5}),
    ('{"a": "unterminated}', {"a":"unterminated"}),
    ('{"a":1 "b":2}', {"a":1,"b":2}),
    ('{foo_bar: [true, false, null]}', {"foo_bar":[True,False,None]}),
    ('{"nested": {inner: "yes",},}', {"nested":{"inner":"yes"}}),
    ('{"path": "C:\\tmp\\file",}', {"path":"C:\\tmp\\file"}),
]

def load_solution():
    spec = importlib.util.spec_from_file_location('solution', Path('solution.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod

def main():
    start=time.time(); sol=load_solution(); correct=0; failures=[]
    for raw, expected in CASES:
        repaired = sol.repair_json(raw)
        try:
            parsed = json.loads(repaired)
            ok = parsed == expected
        except Exception as e:
            parsed = f'ERROR: {type(e).__name__}: {e}'; ok = False
        correct += int(ok)
        if not ok:
            failures.append({'input':raw,'repaired':repaired,'parsed':parsed,'expected':expected})
    acc=correct/len(CASES)
    metrics={'primary_metric':acc,'metric_name':'repair_exact_accuracy','higher_is_better':True,
             'num_cases':len(CASES),'num_correct':correct,'runtime_seconds':round(time.time()-start,4),'failures':failures[:10]}
    Path('metrics.json').write_text(json.dumps(metrics,indent=2,sort_keys=True)+'\n')
    print('---'); print(f'primary_metric: {acc:.6f}'); print('primary_metric_name: repair_exact_accuracy')
    print('higher_is_better: true'); print(f"runtime_seconds: {metrics['runtime_seconds']}"); print('metrics_json: metrics.json')
if __name__=='__main__': main()
