---
name: paper-pdf-translator
description: "Translate academic PDFs in place while preserving layout, figures, line spacing, and selectable/editable text using local PDF/OCR tooling plus agent-side translation."
version: 1.0.0
author: R-Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [PDF, Translation, OCR, Research, Documents, Productivity]
    related_skills: [ocr-and-documents, nano-pdf, read_paper]
---

# Paper PDF Translator

Translate a paper PDF while keeping the original page geometry, figures, tables, formulas, and visual structure as close as possible. The output should be a real PDF with selectable/extractable translated text, not a screenshot-only document.

## Default approach

Do **not** call hosted translation APIs or full PDF translation engines by default. Use local PDF/OCR tooling for layout and let the agent translate extracted chunks:

1. Extract PDF text with page coordinates.
2. Translate the extracted JSONL records in-session.
3. Write translated text back into the original page regions while leaving images and non-text graphics untouched.

Borrow layout ideas from PDFMathTranslate/pdf2zh, but do not execute those engines unless the user explicitly asks for that dependency.

Read [references/tooling.md](references/tooling.md) for dependency setup, OCR notes, and command templates.

## Workflow

1. **Inspect the input PDF**
   - Confirm the file exists and is a PDF.
   - Check whether it has extractable text.
   - If it has almost no embedded text, treat it as scanned and run OCR first.

2. **Extract positioned text**

```bash
python3 skills/productivity/paper_pdf_translator/scripts/pdf_layout_translate.py \
  extract paper.pdf \
  --out-dir paper_translate_work \
  --target zh-CN
```

3. **Translate JSONL chunks**
   - Translate `paper_translate_work/chunks/*.jsonl`.
   - Preserve every field and record order.
   - Fill only the `translation` field.
   - Keep formulas, references, units, variable names, and citation markers intact.
   - Make translations concise enough to fit the original boxes.

4. **Apply translations**

```bash
python3 skills/productivity/paper_pdf_translator/scripts/pdf_layout_translate.py \
  apply paper.pdf paper_translate_work/chunks-translated \
  --output paper.zh-CN.pdf \
  --target zh-CN \
  --fail-on-overflow
```

5. **Validate**

```bash
python3 skills/productivity/paper_pdf_translator/scripts/inspect_pdf_translation.py \
  paper.pdf paper.zh-CN.pdf \
  --render-dir paper_translate_work/renders \
  --render-pages 1,2,3
```

Check that page count matches, images remain present, translated text is selectable, and sampled pages have no obvious overlap, clipping, or broken line spacing.

## OCR guidance

OCR is only a preprocessing step. Use it when the source PDF is scanned/image-only:

```bash
ocrmypdf --deskew --rotate-pages --language eng+chi_sim scanned.pdf scanned.ocr.pdf
```

Then run the extract/apply workflow on `scanned.ocr.pdf`. Scanned PDFs are less reliable for clean in-place replacement because the original text may be part of a background image; validate visually and report any pages needing manual review.

## Quality bar

Do not call the work done only because a command succeeded. A good result has:

- same page count as the input;
- figures/images visually unchanged;
- translated text selectable/extractable from the output;
- no obvious text overlap, clipped lines, or chaotic line spacing on sampled pages;
- formulas and tables still readable.
