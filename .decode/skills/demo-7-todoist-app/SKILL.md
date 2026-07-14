---
name: demo-7-todoist-app
description: Demo skill that builds a single-file index.html todo app in vanilla JS with localStorage — add, complete, and filter (all/active/done) — then opens it in the browser.
---

Build a small todo app as a single self-contained `index.html` file, then open it in the browser.

## Constraints

- **One file, zero dependencies.** Everything — markup, CSS, and JavaScript — lives inside a single
  `index.html`. No build step, no framework, no CDN `<script>` tags: plain vanilla JS only.
- **Persist with `localStorage`.** Todos survive a page reload; there is no backend.
- Keep it small and readable — this is a demo people will open and inspect the source of.

## Features to implement

- **Add** a todo from a text input (Enter or an Add button); ignore empty input.
- **Complete** a todo by toggling it (a checkbox or click), with a struck-through / done style.
- **Filter** the visible list with three views — **all**, **active** (not yet done), and **done** —
  and show which filter is active.
- Optional niceties if they stay simple: delete a todo, and a "N items left" count.

## How to verify

1. Open it: `open index.html` (this launches your default browser on macOS).
2. Add a few todos, complete some, and click through the all / active / done filters.
3. Reload the page and confirm the todos and their done state are still there (that proves
   `localStorage` is wired up).

Report the final `index.html` and the one-line command to open it.
