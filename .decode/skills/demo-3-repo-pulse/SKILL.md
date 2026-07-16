---
name: demo-3-repo-pulse
description: Demo skill that pulls live GitHub API data for a repo, analyses a full year of weekly commit activity and top contributors, and renders a single-file dashboard.html with stat tiles and inline SVG charts — no chart library.
---

Build a **repo pulse dashboard** from live data: probe the GitHub API, pull a repository's last
**52 weeks** of activity, analyse it, and render everything into one self-contained
`.decode/outputs/dashboard.html` the human can open in a browser. All files this demo produces
live under `.decode/outputs/` (unless the human named a different path).

Default target repository: `pydantic/pydantic-ai`. If the human named a different `owner/repo`,
use that instead.

## 1. Probe the data source

Use `web_fetch` to look at the live API and learn the exact field names before writing any code:

- `https://api.github.com/repos/pydantic/pydantic-ai` — note `stargazers_count`, `forks_count`,
  `open_issues_count`, `description`.

That one probe is enough — do not fetch the big endpoints with `web_fetch`; the analysis script
will pull those.

## 2. Write the analysis script

Write `.decode/outputs/pulse.py` and run it with the one dependency brought in just for the run
(do NOT add project dependencies):

```
uv run --with requests python .decode/outputs/pulse.py
```

The script must:

1. GET these three endpoints (plain `requests`, no auth token needed):
   - `https://api.github.com/repos/{owner}/{repo}` — the headline stats.
   - `https://api.github.com/repos/{owner}/{repo}/stats/commit_activity` — the last **52 weeks**
     of commit counts, one entry per week (`total` commits + a Unix `week` timestamp). The whole
     year arrives in this ONE request — never page through `/commits` for it.
   - `https://api.github.com/repos/{owner}/{repo}/contributors?per_page=10` — top contributors
     with their commit counts.
2. The stats endpoint answers **202** while GitHub computes the data: retry a few times with a
   short sleep until it answers 200 with the 52-entry list.
3. Aggregate: commits per week across the 52 weeks (label each week with the ISO date of its
   `week` timestamp), the busiest week of the year, and the top-10 contributor leaderboard.
4. If the API answers 403 (rate limit), say so plainly; if `GITHUB_TOKEN` is set in the
   environment, send it as a `Bearer` header.

## 3. Render the dashboard — charts as inline SVG

Have the script generate `.decode/outputs/dashboard.html` — **one file, zero external requests,
no chart library**. Draw both charts as inline `<svg>` elements built with Python string
formatting (no image files, no JavaScript):

- A header with the repo name and description.
- Four stat tiles: ⭐ stars, 🍴 forks, 🐛 open issues, 👥 contributors shown.
- **Commits per week** — an SVG bar chart: one `<rect>` per week (52 bars), heights scaled to the
  busiest week, a handful of month labels along the x-axis, and a `<title>` tooltip per bar
  (`YYYY-MM-DD — N commits`).
- **Top contributors** — an SVG horizontal bar chart: one `<rect>` per contributor, with the
  login and commit count labelled as `<text>`.
- Simple clean CSS, dark background.

## 4. Verify and report

1. Sanity-check the HTML exists and carries both charts:
   `grep -c "<svg" .decode/outputs/dashboard.html` should print 2.
2. Tell the human to open it: `open .decode/outputs/dashboard.html`.

Report the headline numbers (stars, forks, open issues), the busiest week of the year, the top
contributor, and the one-line command to open the dashboard.
