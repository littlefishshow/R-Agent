# autoresearch program: byte_codec_detector

## Research objective
Improve `solution.decode_text(text: str) -> str` for small mojibake, HTML entity, and escape-sequence snippets.

- Primary metric: decoded_exact_accuracy
- Direction: higher_is_better
- Baseline: minimal entity/escape decoding

## Allowed files
`solution.py`, `train/`, `artifacts/`.

## Fixed files
`eval.py`, `eval.sh`, cases and metrics.

## Baseline
```bash
python3 prepare.py
bash train/train.sh > run.log 2>&1
bash eval.sh > eval.log 2>&1
cat metrics.json
```

## Experiment ideas
Try chained html unescape, unicode_escape, latin1->utf8 mojibake repair, cp1252 handling, common replacements for â€™/â€œ/Â, and scoring candidate decodes by suspicious-character counts. Change one idea per round.

## Completion Criteria
This project is solved only when the official evaluation in `metrics.json` reports:

- `metric_name`: `decoded_exact_accuracy`
- `higher_is_better`: `true`
- `decoded_exact_accuracy >= 1`

Rationale: Exact decoding for all embedded cases.
