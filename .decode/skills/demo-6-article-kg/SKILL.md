---
name: demo-6-article-kg
description: Demo skill that web-fetches three Decoding AI knowledge-graph articles, has the agent itself distill them into a typed entity/relation graph, and renders an interactive dark-themed force-directed KG into one self-contained kg.html — no graph library, no CDN.
---

The hardcore one: turn three live articles into a knowledge graph you can play with in the browser.
You do all of it — you are the extractor (no NLP library) and you are the renderer (no graph
library, no CDN, no framework). Two artifacts, one page that works the instant it lands.

Work the steps in order. Do not skip ahead to the HTML.

## Step 0 — paths and plan

Both artifacts go in the output directory named in the **Output default** line at the very end of
these instructions — unless the human named a destination, which wins. Call it `<OUT>`:

- `<OUT>/graph.json` — the extraction
- `<OUT>/kg.html` — the page

Put the pipeline in `todo_write` as five items (fetch, extract, render, verify, report) and write
the real `<OUT>` path into the first one so you never re-derive it.

## Step 1 — fetch the three sources

One `web_fetch` per URL (the tool returns Markdown):

- https://www.decodingai.com/p/keep-knowledge-graph-clean
- https://www.decodingai.com/p/understanding-neo4j-graph-agent-memory-system
- https://www.decodingai.com/p/ship-a-knowledge-graph-ontology-in-5-minutes

Keep the title and body prose. Ignore navigation, subscribe buttons, CTAs, comments, footers and
trailing "read more" lists. If a fetch fails or comes back truncated, say so plainly and work with
what you have — never invent content you did not fetch.

## Step 2 — extract one merged graph

Distill ONE graph across all three articles: an entity discussed in two articles is ONE node, not
two. Write it to `<OUT>/graph.json`:

```json
{
  "nodes": [
    {"id": "Ontology", "type": "concept", "desc": "one sentence in your own words"},
    {"id": "Neo4j", "type": "tool", "desc": "one sentence in your own words"}
  ],
  "edges": [
    {"source": "Ontology", "target": "KG drift", "label": "prevents"}
  ]
}
```

- **20–35 nodes.** The key ideas, not every noun.
- `type` is exactly one of `concept` / `tool` / `pattern` / `problem`.
- `desc` is one sentence of your own synthesis, not a quote.
- Edges are directed; `label` is a short verb phrase (`prevents`, `stores`, `queries`).
- Every `source` and `target` matches a node `id` exactly — ids are case-sensitive.
- No orphans: every node has at least one edge.

Before moving on, re-read what you wrote and confirm the node count is in range, every edge endpoint
exists, and no node is orphaned. Fixing it here is cheap; fixing it after the page is written is not.

## Step 3 — write the page

**One `write` call produces a finished file.** There is no second pass. The data goes in as a
literal — the actual `{ "nodes": [...], "edges": [...] }` you just wrote to `graph.json`, copied in
full. Never emit `{{GRAPH}}`, `<DATA>`, `/* graph here */`, `...`, or any token you intend to
substitute later, and never write an unexpanded template string. If you catch yourself planning to
"fill in the data next", stop and write the whole file instead.

Author `<OUT>/kg.html` in this order — each section complete before the next:

1. `<head>`: `<title>`, and one `<style>` block. No external stylesheet, script, or font.
2. `<header>`: the page title, the three article URLs as links, and the type legend (a colored dot
   plus the type name, four of them).
3. An empty `<svg>` filling the viewport, and an empty detail card `<div>` positioned on the right.
4. One `<script>` block, in this order: the data, the model, the drawing, the simulation, the
   interactions, the start.

### What goes in the script, in order

**Data.** `const GRAPH = ` followed by the literal object. This is the first statement in the block.

**Model.** Map each node to an object carrying `x`, `y`, `vx`, `vy` and `degree`. Seed positions on
a circle around the viewport center — never all at one point, or the repulsion divides by zero.
Resolve every edge's `source`/`target` string to its node object once, up front, so the loop never
searches by id. Count degree while you do it. Build a neighbor set per node id for the hover step.

**Drawing.** Create the SVG elements once, before the loop starts: a `<line>` and a small `<text>`
per edge, and a `<g>` per node holding a `<circle>` and a `<text>` label. Keep references to them.
The loop only updates coordinates and classes — it never creates or destroys elements. Order the
groups edges → edge labels → nodes so nodes sit on top. Radius is `6 + min(9, degree * 1.4)`; fill
is the node's type color.

**Simulation.** One `tick()` applying three forces, then integrating:

- *Repulsion*, every node pair: magnitude `5000 / distanceSquared`, pushing apart along the line
  between them. Clamp `distanceSquared` to a minimum of 1 and jitter coincident nodes.
- *Springs*, along each edge: pull proportional to `(distance - 110) * 0.01`.
- *Centering*: nudge each node toward the viewport center by `0.0015` of its offset.
- Integrate: multiply velocity by `0.86` damping, add to position, clamp inside the viewport with a
  margin so nothing hides under the header.

Scale all forces by an `alpha` that starts at 1 and decays ~0.5% per tick toward a floor of about
0.06 — that floor is what keeps the layout gently alive instead of frozen. Run ~250 ticks before the
first paint so the page opens settled, then `requestAnimationFrame` a loop of tick + draw.

**Interactions.**

- *Drag*: `pointerdown` on a node pins it and captures the pointer; `pointermove` sets its position
  and zeroes its velocity; `pointerup` unpins and releases. Bump `alpha` on grab so the graph
  re-settles around it.
- *Hover*: highlight the node and its edges, dim every non-neighbor, and fill the detail card with
  the node's type, id, `desc`, and its relations — outgoing as `label → target`, incoming as
  `source label →`. Clear it on leave, but not mid-drag.

**Design.** Dark, minimal, no framework: CSS variables for the palette and the four type colors, a
system font stack, full-viewport canvas, sticky translucent header, the detail card as a fixed
rounded panel, a subtle glow on the hovered node, and dimming via opacity.

## Step 4 — verify

One `bash` call. All four must hold:

```bash
python3 -m json.tool <OUT>/graph.json > /dev/null && echo "json ok"
grep -c 'src="http' <OUT>/kg.html                        # 0 — nothing loaded from the network
grep -Ec '\{\{|<DATA>|graph here|\.\.\.' <OUT>/kg.html   # 0 — no leftover placeholder
grep -c 'const GRAPH' <OUT>/kg.html                      # 1 — the data is inlined
```

Then confirm the page carries the whole graph: count `"id":` occurrences in `kg.html` and in
`graph.json` and check they match. Any mismatch means the data was truncated — rewrite the file in
full, do not patch it.

## Step 5 — report

Node count by type, edge count, the three most-connected entities, one cross-article relation that
surprised you, and the command to open it:

```bash
open <OUT>/kg.html
```
