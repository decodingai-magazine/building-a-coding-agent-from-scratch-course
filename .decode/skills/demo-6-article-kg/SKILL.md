---
name: demo-6-article-kg
description: Demo skill that web-fetches three Decoding AI knowledge-graph articles, has the agent itself distill them into a typed entity/relation graph, and renders an interactive dark-themed force-directed KG into one self-contained kg.html — no graph library, no CDN.
---

The hardcore one: turn three live articles into a knowledge graph you can play with in the
browser. Fetch the articles, distill them into typed entities and labeled relations — **you are
the extractor**, no NLP library — and render an interactive force-directed graph into one
self-contained HTML page.

Two artifacts, both under `.decode/outputs/` (unless the human named a different path):
`graph.json` (the extraction checkpoint) and `kg.html` (the visualization).

## Plan first

Lay out the pipeline with `todo_write` (fetch → clean → extract → render → verify) and tick
items off as you go.

## 1. Fetch and clean the sources

`web_fetch` each article (the tool returns Markdown):

- https://www.decodingai.com/p/keep-knowledge-graph-clean
- https://www.decodingai.com/p/understanding-neo4j-graph-agent-memory-system
- https://www.decodingai.com/p/ship-a-knowledge-graph-ontology-in-5-minutes

Clean each one before extracting: keep the title and the body prose; drop navigation, subscribe
buttons and CTAs, comment sections, footers, and trailing "read more" link lists. If a fetch
fails or comes back truncated, say so plainly and work with what you have — never invent content
you did not fetch.

## 2. Extract the graph — you are the extractor

Read the cleaned articles and distill ONE merged graph across all three (an entity shared by two
articles appears once). Write it to `.decode/outputs/graph.json`:

```json
{
  "nodes": [
    {"id": "Ontology", "type": "concept", "desc": "one-sentence synthesis in your own words"},
    {"id": "Neo4j", "type": "tool", "desc": "..."}
  ],
  "edges": [
    {"source": "Ontology", "target": "KG drift", "label": "prevents"}
  ]
}
```

- `type` is one of `concept` / `tool` / `pattern` / `problem` — pick the best fit.
- Edges are **directed** and labeled with a short verb phrase (`prevents`, `stores`, `queries`).
- **20–35 nodes total**: the key ideas, not every noun. No orphan nodes — everything connects;
  every edge endpoint must be an existing node `id`.
- `desc` is one sentence of YOUR synthesis, not a quote.

## 3. Render — one self-contained interactive page

Author `.decode/outputs/kg.html` by hand — **one file, zero external requests, no graph
library** (nothing loaded from a CDN: no external scripts, stylesheets, or fonts). Inline
everything:

- The graph data inlined as `const GRAPH = { ... };` — paste the **literal JSON** from
  `graph.json` right there as the value (open brace, real `"nodes"` and `"edges"` arrays, close
  brace). Do **NOT** leave a placeholder or template token — no `{{GRAPH}}`, no `<DATA>`, no
  "insert graph here", nothing meant to be substituted in a later pass. There is no second pass:
  the single write must already contain every node and edge, and the file must be openable and
  fully working the instant it lands. If you built the page from a template string, expand it
  before writing — never write the unexpanded template.
- A hand-rolled **force simulation in vanilla JS** (~80 lines) drawing into an inline `<svg>`:
  pairwise repulsion, springs along edges, gentle centering; let it keep settling live after an
  initial burst of ticks.
- **Drag**: pointer-grab any node and the layout re-settles around it.
- **Hover**: highlight the node, its edges, and its neighbors; dim the rest; fill a side detail
  card with the node's `desc` and its relations rendered as `prevents → KG drift` lines.
- Node color by `type` (legend in the header), node radius by degree, edge labels as small
  `<text>` along each line.
- **Design — dark minimal, modern without a framework**: full-viewport canvas; sticky header
  with a title, the three source articles as links, and the type legend; the hover detail card
  as a fixed side panel; CSS variables, system font stack, subtle glow on nodes.

## 4. Verify and report

1. Validate the checkpoint:
   `uv run python -m json.tool .decode/outputs/graph.json > /dev/null` succeeds, and a one-liner
   confirms every edge endpoint is a node id and the node count sits in 20–35.
2. The page carries real data, not a template: `grep -c "const GRAPH" .decode/outputs/kg.html`
   prints 1, and `grep -Ec '\{\{|\}\}|insert .*here|<DATA>' .decode/outputs/kg.html` prints 0 —
   any leftover substitution token means the data was never inlined; fix it before reporting.
3. The page is self-contained: `grep -c 'src="http' .decode/outputs/kg.html` prints 0. Confirm
   the inlined node/edge counts match `graph.json` (e.g. count `"id":` occurrences in each).
4. Tell the human to open it: `open .decode/outputs/kg.html`.

Report the node count by type, the edge count, the three most-connected entities, one relation
across articles that surprised you, and the one-line command to open the page.
