---
name: summarize-substack
description: Fetch a Substack article, clean it to just the body with Python, then write a structured Markdown summary — executive summary, problem/solution/transformation, a Mermaid visual explanation, and how to apply it as an AI engineer.
---
You turn a Substack URL into a structured summary a busy AI engineer can read in two minutes. A bundled
Python script does the fetching and cleaning; you do the reading and the writing. Ground every line in
the cleaned article — never summarize from the title or your prior knowledge.

Before you write, `read` the bundled `references/template.md` — it is the exact output structure and
holds the Mermaid recipes. Follow it.

## Input

The Substack article URL. If the user's message doesn't contain one, call `ask_user` for it.

## 1. Fetch + clean (Python)

Run the bundled script with `bash`. It fetches the page, strips the subscribe/share/comment chrome, and
writes a clean Markdown copy of just the article body (needs `uv` + network in the sandbox):

```
uv run .decode/skills/summarize-substack/scripts/fetch_substack.py "<url>"
```

It writes `./<slug>.cleaned.md` and prints the path, title, author, date, word count, and a preview. If
it reports a **short body**, the post is likely paywalled — say so in the summary and work only from the
preview you have. If the fetch fails, report the exact error and stop (don't summarize from the URL).

## 2. Read the cleaned article

`read` the `.cleaned.md` file the script wrote (use `offset`/`limit` for long pieces). That file — not
the live page — is your source for everything below.

## 3. Write the summary

Following `references/template.md`, write `./<slug>-summary.md` with these sections:

- **Executive summary** — the thesis + 2–3 key takeaways in ~5 sentences.
- **Problem → Solution → Transformation** — the gap the article addresses, what it proposes, and the
  before→after payoff.
- **Visual explanation** — one or more Mermaid diagrams that make the article's argument visual; pick
  the diagram type that fits its structure (recipes are in the template), and caption each.
- **Apply it as an AI engineer** — concrete practices to adopt, pitfalls to watch, and the smallest
  experiment you could run this week.

Every diagram and claim must reflect the article you read — no invented steps. Finish by reporting the
path to the summary and a two-line gist.
