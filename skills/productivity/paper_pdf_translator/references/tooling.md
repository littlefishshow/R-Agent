# Local PDF Translation Tooling

## Principle

Default execution must not depend on hosted translation APIs or full PDF translation engines. Use local PDF/OCR tools to preserve layout, and let the agent translate extracted text chunks in-session.

Mature tools such as PDFMathTranslate/pdf2zh are useful references for layout strategy: detect text boxes, avoid formulas/figures, preserve page geometry, and fit translated text back into bounded regions. Do not call them unless the user explicitly permits using that engine.

## Dependencies

Required for born-digital PDF editing:

```bash
python3 -m pip install pymupdf
```

Optional OCR for scanned PDFs:

```bash
python3 -m pip install ocrmypdf
```

System OCR may also require Tesseract language packs, for example `chi_sim` for Simplified Chinese. If `ocrmypdf` or `tesseract` is unavailable, report that scanned PDFs cannot be reliably converted to selectable text in the current environment.

## Born-Digital PDF Workflow

Extract layout-aware text chunks:

```bash
python3 skills/productivity/paper_pdf_translator/scripts/pdf_layout_translate.py \
  extract paper.pdf \
  --out-dir paper_translate_work \
  --target zh-CN
```

Translate each `paper_translate_work/chunks/*.jsonl` file. Preserve record order and fields; fill only `translation`.

Apply translations:

```bash
python3 skills/productivity/paper_pdf_translator/scripts/pdf_layout_translate.py \
  apply paper.pdf paper_translate_work/chunks-translated \
  --output paper.zh-CN.pdf \
  --target zh-CN \
  --erase-mode redact \
  --fail-on-overflow
```

If some text does not fit, inspect the generated `*.fit_failures.json`, shorten translations, or retry with smaller text:

```bash
python3 skills/productivity/paper_pdf_translator/scripts/pdf_layout_translate.py \
  apply paper.pdf paper_translate_work/chunks-translated \
  --output paper.zh-CN.pdf \
  --target zh-CN \
  --font-scale 0.82 \
  --min-font-size 4.8
```

## Scanned PDF Workflow

Run OCR first:

```bash
ocrmypdf --deskew --rotate-pages --language eng+chi_sim scanned.pdf scanned.ocr.pdf
```

Then run the born-digital workflow against `scanned.ocr.pdf`. Scanned PDFs are harder: if the original page is a single background image, local overlay can hide text regions but cannot truly recover the original clean background behind the text. Validate visually.

## Font Guidance

For Chinese output, the apply script defaults to PyMuPDF's built-in `china-s` font. If it renders poorly in the local PDF viewer, pass a known CJK font:

```bash
--fontfile /path/to/NotoSansCJK-Regular.ttc --fontname custom-cjk
```

Use a font with broad CJK coverage for Chinese/Japanese/Korean targets. Avoid Latin-only fonts such as DejaVu for Chinese final output.

## Validation

Basic inspection:

```bash
python3 skills/productivity/paper_pdf_translator/scripts/inspect_pdf_translation.py \
  paper.pdf paper.zh-CN.pdf \
  --render-dir paper_translate_work/renders \
  --render-pages 1,2,3
```

Look for:

- same page count;
- extractable text in the output;
- images still present;
- no obvious overlap, clipping, or chaotic line spacing in rendered sample pages.

## When To Use External Engines

Only use `pdf2zh`, DeepL, OpenAI translation APIs, or similar systems if the user explicitly approves that specific dependency. If used, still validate output with `inspect_pdf_translation.py` and report the dependency clearly.
