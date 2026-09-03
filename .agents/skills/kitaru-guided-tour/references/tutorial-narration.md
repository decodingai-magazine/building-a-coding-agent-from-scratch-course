# Friendly tutorial narration

Guide the user through Kitaru as the tour runs. Preserve the low-friction route, but do not make the agent's work feel like unexplained setup followed by a result. By the end, the user should understand both what Kitaru produced and why each object exists.

Assume the user has not read the sample agent, has not used Kitaru before, and does not know its vocabulary. Explain from the concrete returns example first. Introduce a product term only when the current step makes its purpose visible.

Use only terms that exist in Kitaru's product, CLI, API, or documentation when naming an object. Do not coin a noun for a temporary explanation, group of fields, or stage in the tour, and do not replace an official object with a friendly-sounding alias. Explain an unfamiliar Kitaru term from the concrete example, then use that term consistently. Use ordinary verbs and sentences for everything else. For example, say “Here is what the experiment will do” rather than naming the summary a “run card,” and say “the agent configuration has changed” rather than teaching an “agent fingerprint.”

## Use a teaching rhythm

Narrate each meaningful transition in two parts.

Before the action, use a short, natural paragraph to:

1. connect the step to the story so far;
2. explain what is about to happen in ordinary language;
3. say why the step is necessary; and
4. tell the user what they will see or be able to do afterward.

After the action, state what now exists or changed, why that result matters, and how it leads to the next step. Use two to four sentences when the concept is new. Use less when the user has already learned it.

Do not narrate every command, file read, schema lookup, retry, or status check. Do explain installations, server selection, registration, imports, durable review writes, and evaluator runs because they change the user's environment or create a new Kitaru concept. When a command updates or installs something, explain what capability it provides and why the tour needs it before asking for approval.

Avoid empty narration such as “Now I will create an investigation.” Explain the purpose instead:

> We have three past runs with useful evidence. I’m going to put them in a Kitaru investigation so you can inspect them in order and leave a durable judgment beside each trace.

Keep the tone warm and confident. Invite the user along without quizzing them, presenting optional branches, or making them repeat explanations before continuing.

## Teach the journey in context

### Connect to Kitaru

Explain that a Kitaru server is where agents, recorded runs, annotations, and review results are stored. If the user already has a healthy local or cloud server selected, say that the tour will keep using it. If setup is missing, explain the smallest required change and why it unlocks the next step.

When CLI or MCP setup is relevant, explain the distinction without turning it into a choice the user must make: the CLI and MCP are two ways for the coding agent to work with the same Kitaru server. A working project-local CLI is enough for the tour; MCP can make later agent interactions more direct.

### Identify the agent and its history

Before registration, explain that Kitaru ties recorded behavior to an exact agent version so later findings can be traced back to the code that produced them. Registration does not run the agent or judge its quality; it gives that code version a durable identity.

Before import, explain that the template includes ten already-recorded customer-support runs. Importing them gives Kitaru the complete sequence of model messages, tool calls, and tool results, which is what lets the user inspect what actually happened rather than trusting the final reply alone.

After registration or import, state the exact durable result in approachable terms. For example: “Kitaru now knows which version of the returns agent produced these ten runs, so we can review its behavior without running or paying for the model again.”

### Prepare the review

Before adding observations and highlights, explain that the coding agent has read the complete traces and will point to evidence worth noticing. An annotation records the observation; a highlight places it beside the exact tool result, message, or field that supports it. These are prepared suggestions, not the user's verdict.

Before creating the investigation, explain that it turns the three selected runs into one ordered frontend review. The direct questions make the behavior easy to judge, while the verdict buttons record the human decision in a consistent form that Kitaru can use later.

After creation, say what was saved and make the division of labor explicit: the coding agent prepared the evidence, Kitaru keeps it attached to the trace, and the user decides Acceptable, Problematic, or Uncertain.

### Turn judgment into a reusable check

After the user returns, begin with what their verdicts established. Then explain that a cohort is a frozen set of the exact reviewed examples: the problematic behavior plus an acceptable comparison that marks its boundary. Freezing the membership prevents the meaning of the check from drifting later.

Before evaluator selection or creation, explain that an evaluator is a repeatable rule applied to recorded runs. The tour is turning the behavior the user just confirmed into a narrow check, then running that check across all ten sessions to see whether the same pattern appears elsewhere. An evaluator version records the exact rule used so the result remains reproducible.

After the run, lead with the result in plain language. Only then connect the result back to the terms: the reviewed examples became the cohort, the reusable rule became the evaluator version, and Kitaru preserved both beside the evidence and human judgments.

Send the user to the cohort and evaluator pages next. Explain what each page preserves and ask them to come back after they have looked. Do not continue into experiment setup until they return.

### Test one bounded improvement

After the user returns, explain the difference between measuring recorded behavior and testing a change. The experiment will start fresh agent tasks from the reviewed cases' stored top-level inputs, apply one small candidate override, and compare the resulting sessions with the recorded evidence using the exact evaluator the user just inspected. It does not restore conversation checkpoints, process memory, files, or the original external world.

Before experiment creation, briefly explain the proposed change, tool behavior, expected model work, cost uncertainty, and what the comparison can establish. Let the replay-experiment skill obtain approval after that explanation. Do not make paid execution feel like an automatic consequence of completing the review.

After settlement, lead with the experiment page. Explain how to select the run, where the baseline and candidate evidence appear, and what improved, regressed, stayed unchanged, failed, or remained missing. Ask the user to inspect it and return before the tour closes.

## Keep explanations honest

- Explain what the current action actually proves. Registration identifies code; it does not validate behavior. Import preserves recorded evidence; it does not make the evidence representative.
- Distinguish the coding agent's prepared observations from the user's verdicts every time that distinction becomes relevant, without repeating a warning mechanically.
- Do not claim prevalence from three curated teaching examples. The full ten-session evaluator result can describe this synthetic population only.
- If a step is resumed rather than created, explain that Kitaru already has the durable result and the tour is reusing it. This teaches persistence better than pretending the work happened again.
- If something fails, preserve the lesson: name the intended concept, the exact blocked action, and the smallest recovery. Do not replace the tutorial with raw command output.
