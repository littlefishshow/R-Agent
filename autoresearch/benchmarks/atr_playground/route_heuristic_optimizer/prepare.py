#!/usr/bin/env python3
from pathlib import Path
Path('outputs').mkdir(exist_ok=True)
Path('artifacts').mkdir(exist_ok=True)
print('prepare: no external data needed; deterministic benchmark is embedded in eval.py')
