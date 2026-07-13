---
id: 114
feature: evals
status: pending
---

# Author regression probes 15–20 (memory, compaction, groundedness, contracts)

Depends on: 111. Implements ADR-0017 §2,6,7.

## Scope

15. `15-memory-obedience` — fixture `AGENTS.md` with an unambiguous naming rule (e.g. "every new
    python file starts with `dc_`"); task creates a file. C: created filename obeys (glob check);
    fall back to a judge only if the mechanical check proves brittle.
16. `16-compaction-survival` — pre-filled near-limit conversation (fixture builder from 111)
    carrying one early fact; prompt asks to recall it, compaction fires. C: `Contains` the fact in
    the answer.
17. `17-grounded-answer` — fixture source document; a question answerable only from it. J: G-Eval
    faithfulness vs the source.
18. `18-no-hallucinated-files` — ask about `does_not_exist.py` in a seeded tree. J: response says
    it's not found and invents nothing (criteria spelled out for the judge).
19. `19-template-compliance` — prompt embeds a required report template. C: `Contains` each
    required section header + J: adherence judge.
20. `20-json-output-contract` — "answer ONLY as JSON matching {schema}". C: `IsJson` + a schema-
    validation check (pydantic model in the probe).

## Acceptance Criteria

- [ ] Six probes registered and smoke-tested offline (fixtures build; metric/judge bindings
      resolve through the 104 factory).
- [ ] 16's prefilled history actually crosses the compaction threshold under the configured window
      (unit-asserted against decode's compaction settings).
- [ ] Spot-run one judge-backed probe against a real model; result logged.
- [ ] `make ci` green.

## Out of scope

- Threshold values (115).

## Log
