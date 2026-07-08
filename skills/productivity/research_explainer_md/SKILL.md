---
name: "research_explainer_md"
description: "调研资料并生成通俗易懂的中文解析 Markdown"
---

# Research Explainer Markdown

## When to Use

Use this skill when the user asks to summarize online/source-analysis materials into a Markdown document, especially when they want a beginner-friendly or child-friendly explanation.

## Procedure

1. Clarify the target topic from the user request; if the target is obvious, proceed without asking.
2. Gather grounding material:
   - Search the web for official docs and credible analyses.
   - Prefer official documentation, package metadata, release notes, and directly observable artifacts over unverifiable blog claims.
   - If source code is not truly open, explicitly state that the document is architecture/behavior analysis rather than official line-by-line source commentary.
3. Inspect local workspace to choose an output location; default to `outputs/<topic>_解析_通俗版.md` unless the user specifies otherwise.
4. Structure the Markdown for accessibility:
   - Start with a plain-language one-sentence explanation.
   - Use analogies, tables, diagrams, and step-by-step examples.
   - Separate verified facts from reasoned architecture inference.
   - Include a “risks/boundaries” section and a “references” section.
5. Verify the generated file:
   - Use `wc -l` or equivalent to confirm it exists and has content.
   - Read the opening lines or headings to catch formatting mistakes.
6. Final response should give the exact output path and a short summary of what was included.

## Notes

- Do not present reverse-engineered internals as certain unless directly verified.
- For calculations, current versions, package metadata, and file verification, use tools rather than memory.
- If the task involved many tool calls or a reusable workflow improvement, update this skill.