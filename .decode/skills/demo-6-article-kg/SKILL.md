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

- The graph data inlined as `const GRAPH = {...}` — the same nodes and edges as `graph.json`.
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
2. The page is self-contained: `grep -c "const GRAPH" .decode/outputs/kg.html` prints 1 and
   `grep -c 'src="http' .decode/outputs/kg.html` prints 0.
3. Tell the human to open it: `open .decode/outputs/kg.html`.

Report the node count by type, the edge count, the three most-connected entities, one relation
across articles that surprised you, and the one-line command to open the page.
