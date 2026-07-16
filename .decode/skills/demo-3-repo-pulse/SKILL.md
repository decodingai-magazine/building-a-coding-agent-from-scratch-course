---
name: demo-3-repo-pulse
description: Demo skill that pulls live GitHub API data for a repo, analyses commit activity and contributors, and renders a single-file dashboard.html with stat tiles and embedded matplotlib charts.
---

Build a **repo pulse dashboard** from live data: probe the GitHub API, pull a repository's recent
activity, analyse it, and render everything into one self-contained `dashboard.html` the human can
open in a browser.

Default target repository: `pydantic/pydantic-ai`. If the human named a different `owner/repo`,
use that instead.

## 1. Probe the data source

Use `web_fetch` to look at the live API and learn the exact field names before writing any code:

- `https://api.github.com/repos/pydantic/pydantic-ai` — note `stargazers_count`, `forks_count`,
  `open_issues_count`, `description`.

That one probe is enough — do not fetch the big endpoints with `web_fetch`; the analysis script
will pull those.

## 2. Write the analysis script

Write `pulse.py` and run it with dependencies brought in just for the run (do NOT add project
dependencies):

```
uv run --with requests,matplotlib python pulse.py
```

The script must:

1. GET these three endpoints (plain `requests`, no auth token needed):
   - `https://api.github.com/repos/{owner}/{repo}` — the headline stats.
   - `https://api.github.com/repos/{owner}/{repo}/commits?per_page=100` — recent commits; take
     each commit's author date.
   - `https://api.github.com/repos/{owner}/{repo}/contributors?per_page=10` — top contributors
     with their commit counts.
2. Aggregate: commits per ISO week from the 100 recent commits, and the top-10 contributor
   leaderboard.
3. Render two PNG charts with matplotlib into `charts/`:
   - `charts/commits_per_week.png` — a line or bar chart of commits per week.
   - `charts/top_contributors.png` — a horizontal bar chart of the top contributors.
4. If the API answers 403 (rate limit), say so plainly and retry once with a smaller `per_page`;
   if `GITHUB_TOKEN` is set in the environment, send it as a `Bearer` header.

## 3. Render the dashboard

Have the script (or a small second script) generate `dashboard.html` — **one file, zero external
requests**:

- A header with the repo name and description.
- Four stat tiles: ⭐ stars, 🍴 forks, 🐛 open issues, 👥 contributors shown.
- The two charts embedded inline as base64 `data:image/png;base64,...` `<img>` tags (read the
  PNGs and embed them from Python — never reference the files by path, the HTML must work
  standalone).
- Simple clean CSS, dark background, no JavaScript needed.

## 4. Verify and report

1. Sanity-check the HTML exists and embeds two images: `grep -c "data:image/png" dashboard.html`
   should print 2.
2. Tell the human to open it: `open dashboard.html`.

Report the headline numbers (stars, forks, open issues), the busiest week, the top contributor,
and the one-line command to open the dashboard.
