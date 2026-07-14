---
name: demo-4-data-detective
description: Demo skill that cleans a messy sales CSV, analyses it, and emits a written report with matplotlib charts.
---

Play data detective on a messy sales export: clean it, analyse it, and produce a short report with
charts.

The raw data is `references/messy_sales.csv`. It is deliberately dirty — expect all of:

- **Duplicate rows** — the exact same order recorded more than once.
- **Mixed date formats** — the `order_date` column mixes ISO (`2024-01-15`), US slash
  (`01/15/2024`), and named-month (`15-Jan-2024`) styles.
- **Currency strings** — the `amount` column holds values like `$1,234.56` and `€99` instead of
  plain numbers.
- **Missing values** — some cells (e.g. `region`, `amount`) are blank.

## Tooling

Do NOT add project dependencies. Run your analysis script with the deps brought in just for the run:

```
uv run --with pandas,matplotlib python your_analysis.py
```

## What to produce

1. **Clean** the data:
   - Drop exact duplicate rows.
   - Parse `order_date` into real dates regardless of the incoming format.
   - Strip currency symbols, thousands separators, and stray characters from `amount` and convert
     it to a number.
   - Decide and document how you handle missing values (drop vs. impute) — state it in the report.
2. **Analyse** it: at least total and average sale, sales by region, and a sales-over-time trend.
3. **Emit** the results:
   - `report.md` — what you cleaned, the decisions you made, and the headline findings.
   - PNG charts saved under `charts/` (e.g. `charts/sales_by_region.png` and
     `charts/sales_over_time.png`), referenced from the report.

Report the cleaning steps you took (with before/after row counts) and the key findings.
