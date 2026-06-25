# Memory maintenance

How to keep this memory graph useful. Domain-agnostic; applies to every memory here.

## Discovery model
- `mem:core` is the **graph root** — start there. It indexes every other memory.
- A memory refers to another with the `mem:<name>` convention (e.g. `mem:tech_stack`).
- The *referring* memory says when to read the target; targets don't describe their own when-to-read.
- Nested topics live in folders (e.g. `project/dev_environment`).

## Style
- Dense agent notes, **not** prose docs. Invariants, terse bullets, tables, code over prose.
- For a non-obvious rule, lead with the rule, then **Why:** and **How to apply:** when it aids judgement.
- Record absolute dates, not "yesterday".

## What to write
- Stable, non-obvious project facts: invariants, decisions, gotchas, machine/hardware setup, measured numbers.
- After: a component implemented, a non-obvious decision, a reusable pattern, or a tricky bug fixed.

## What NOT to write
- One-off task state, generic Python/library knowledge, anything already in `.claude/` docs or CLAUDE.md,
  anything re-derivable by reading current code or `git log`.

## Naming
`architecture/<component>`, `decisions/<topic>`, `patterns/<pattern>`, `bugs/<issue>`, `project/<area>`.
One memory per logical unit. Update or delete a memory when it goes stale; never duplicate.
