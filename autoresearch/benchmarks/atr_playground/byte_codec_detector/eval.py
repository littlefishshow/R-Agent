#!/usr/bin/env python3
from __future__ import annotations
import json, time, importlib.util
from pathlib import Path

CASES = [
    ('hello world', 'hello world'),
    ('caf\u00e9', 'café'),
    ('Tom &amp; Jerry', 'Tom & Jerry'),
    ('price &euro;10', 'price €10'),
    ('Itâ€™s fine', "It’s fine"),
    ('â€œquotedâ€\x9d', '“quoted”'),
    ('MÃ¼nchen', 'München'),
    ('naÃ¯ve approach', 'naïve approach'),
    ('FranÃ§ois', 'François'),
    ('Â£5', '£5'),
    ('\u4f60\u597d', '你好'),
    ('smile \ud83d\ude03', 'smile 😃'),
    ('A&nbsp;B&nbsp;C', 'A B C'),
    ('Rock &amp; Roll â€“ live', 'Rock & Roll – live'),
]

def load_solution():
    spec = importlib.util.spec_from_file_location('solution', Path('solution.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod

def main():
    start=time.time(); sol=load_solution(); correct=0; failures=[]
    for raw, expected in CASES:
        try:
            pred=sol.decode_text(raw)
        except Exception as e:
            pred=f'ERROR: {type(e).__name__}: {e}'
        ok=pred==expected; correct+=int(ok)
        if not ok: failures.append({'input':raw,'expected':expected,'pred':pred})
    acc=correct/len(CASES)
    metrics={'primary_metric':acc,'metric_name':'decoded_exact_accuracy','higher_is_better':True,
             'num_cases':len(CASES),'num_correct':correct,'runtime_seconds':round(time.time()-start,4),'failures':failures[:10]}
    Path('metrics.json').write_text(json.dumps(metrics,indent=2,sort_keys=True)+'\n')
    print('---'); print(f'primary_metric: {acc:.6f}'); print('primary_metric_name: decoded_exact_accuracy')
    print('higher_is_better: true'); print(f"runtime_seconds: {metrics['runtime_seconds']}"); print('metrics_json: metrics.json')
if __name__=='__main__': main()
