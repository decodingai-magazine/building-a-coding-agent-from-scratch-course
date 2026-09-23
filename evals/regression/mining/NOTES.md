# Mining session — 2026-09-11, `decode-prod`

What one **Trace Mining** pass over the live project found, what became a
[Regression Case](../README.md#mined-cases), and what was deliberately left alone (ADR-0022 §8,
task 164). The raw command output is committed beside this file; nothing here carries a payload —
the `--json` document is trace ids, thread ids, timestamps, metadata and the exception TYPE, which is
all the `mine` command emits (checked for hostnames, absolute paths and keys before committing).

## The runs

```bash
# the session of record — 30 days, every preset, uncapped JSON
python -m evals mine --preset all --since 2026-08-12T00:00:00Z --limit 200 --json   # → 2026-09-11.json
#   5 traces in 4 signatures

# widened to the whole project, because the 30-day window held no decode-side error at all
python -m evals mine --preset errors --limit 200 --json   # → 2026-09-11-errors-unwindowed.json
#   4 traces in 3 signatures (two of them from 2026-07)
```

The second run is why two files are committed: the 30-day window's only `errors` hits were a 503 and
a request-ceiling stop, so the window was widened for the `errors` preset alone — and that is where
the one real decode-side failure lives (`019f5cd5`, 2026-07-13). Case 21's `source_trace_id` points
at it, so the artifact it came from is committed too rather than being a claim in a log.

`--since` needs a timezone-aware stamp; `--since 2026-08-12` (as the task wrote it) is refused by
design, so the run used `2026-08-12T00:00:00Z`.

## Every signature, and the verdict

| Signature | Traces | Verdict |
|---|---|---|
| `errors \| UnexpectedModelBehavior \| - \| -` | `019f5cd5` | **PICKED** → case `21-empty-model-response` |
| `long \| - \| bash \| -` | `01a08614`, `01a0861b` | **PICKED** → case `22-guessed-file-path` (the behavior inside them, not the token bill) |
| `errors \| ModelHTTPError \| - \| -` | `019f60fd`, `01a08d78` | skipped — provider outage (both 503) |
| `errors \| UsageLimitExceeded \| glob \| -` | `01a08d79` | skipped — designed request ceiling |
| `denied \| - \| read \| gemini-3.5-flash` | `01a08f1a` | skipped — task 163's own deliberate gate probe |
| `low-quality` | none | the `response_quality` rule is one day old; empty window |
| *(the known `ModelHTTPError 400`)* | not in Opik | not reproducible offline — no case (see "Case zero" below) |

## What was picked, and why

### `21-empty-model-response` — trace `019f5cd5-…` (thread `c72c45fb-…`, 2026-07-13)

Four `chat gemini-2.5-flash` spans in a row returned
`{"role": "assistant", "parts": [], "finish_reason": "stop"}` — an assistant turn with nothing in it.
pydantic-ai retries an unusable response three times, then raises
`UnexpectedModelBehavior: Exceeded maximum output retries (3)`: terminal error, zero tool calls, no
answer. The user had asked for one shell command and its output. This is the only trace in the whole
project whose failure is decode's own behavior rather than the provider's availability or a flag the
operator set, so it is the session's primary pick. `fixed_in: unfixed` — nothing on this branch
changes how a model returning empty turns ends a run.

### `22-guessed-file-path` — traces `01a08614-…` + `01a0861b-…` (2026-09-09)

Both runs of *"add a hello line to README and commit"* opened with `read(path="README")` in a tree
whose readme is `README.md`; the span carries `ToolRetryError` and a whole model leg goes on
recovering (run 1 re-guesses `README.md`, run 2 falls back to `glob("README*")`). Two runs out of
two, so it is a behavior, not a coin flip — and it is exactly the kind of thing an invented probe
would never have thought to write down.

Note what was NOT taken from these traces: the `long` preset flagged them for ~100k prompt tokens,
but ~88k of that is cache-read repo context (they are Modal headless runs against the decode repo
itself). The token bill is the repository's size, not a regression; the wasted leg is.

## What was skipped, and why

* **`ModelHTTPError` ×2 (`019f60fd`, `01a08d78`)** — both are **503**s
  (`status_code: 503, model_name: gemini-2.5-flash` and `…, model_name: Qwen/Qwen3.6-35B-A3B-FP8`),
  i.e. the provider was unavailable, a transport failure rather than a decode behavior. There is no decode behavior an offline case could hold to
  here: the regression harness drives the REAL provider through `run_agent_once`, so it has no seam
  to inject a 5xx, and the friendly-line-and-exit-1 handling of one is CLI-level behavior already
  pinned by unit tests. Inventing a case that re-asks the same prompt and asserts "an answer came
  back" would pass forever and prove nothing — the padding ADR-0022 §9 warns against.
* **`UsageLimitExceeded` (`01a08d79`)** — the run called `glob` once and then hit
  `request_limit of 1`. That ceiling is *designed* (`decode run --max-requests`, task 150 / ADR-0020
  §9: one friendly line, empty stdout, exit 1) and the trace is someone exercising it. The eval
  driver caps requests with its own graceful stop (`CAP_STOP_TEXT`), so the harness cannot even
  reproduce the raise. Nothing to gate.
* **`denied` (`01a08f1a`)** — task 163's SWE recorded this run on purpose to give the `denied` preset
  live ground truth (`write` + `bash` denied by the gate). Mining a deliberate probe back into a case
  would be circular, and case `13-permission-deny-respect` already covers the behavior.
* **`low-quality`** — empty: the `response_quality` online rule was created on 2026-09-10, so almost
  no trace in the window carries its score yet. Worth re-mining once it has a few weeks of scores.

## Case zero — the `ModelHTTPError 400`

The known failure is NOT in the live Opik project: both `ModelHTTPError` traces there are 503s. Its
evidence lived in the managed Kitaru workspace, which answers `HTTP 404` today (`kitaru status`), so
the offending request body cannot be read back.

So the reproduction was attempted from first principles instead: the 400 means "decode sent the
provider a request it refused", and the likeliest way a *coding agent* does that is by re-sending a
history holding something malformed. Three fixtures were run for real against the live `gemini` route
(each one turn, cents):

| Fixture history | Result |
|---|---|
| assistant turn with **empty parts** (the shape trace `019f5cd5` recorded) | answered normally, no error |
| assistant turn with an **empty text part** | answered normally, no error |
| ends in an **orphan tool call** with no result | answered normally — and decode logs `healing 1 unprocessed tool call(s) left by a crashed or aborted turn` first (`AgentTurnHandler._heal_dangling_tool_calls`) |

None reproduce a 400, and the third shows decode already defending the most plausible
malformed-history path. No case ships for it: a case that cannot fail proves nothing.

## Provenance in Opik

Each picked trace is tagged `regression-case` in the live project
(`client.update_trace(id, tags=[…])`, existing tags preserved), so the online view links a bad run to
the case that now guards it: `019f5cd5`, `01a08614`, `01a0861b`.
