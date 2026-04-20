# <Feature Name> Plan

## Goal

Implement <brief outcome> for <skill or repo area>.

## Scope

- <in-scope change>
- <in-scope change>

## Non-Goals

- <out-of-scope behavior>
- <out-of-scope behavior>

## Task Package

This file is the single source of truth for Ralph execution. The installed
Ralph runner parses executable tasks from this checklist.

- [ ] **Task 01**: <task description> `tasks/01-first-task.md`
- [ ] **Task 02**: <task description> `tasks/02-second-task.md`

## Validation Strategy

- Run `uv run python scripts/validate-skill-examples.py` after markdown, reference, or
  SQL example changes.
- Run `uv run python tests/test_skill_scripts.py -q` after script or test changes.
- Keep every task verification deterministic and non-interactive.

## Risks

- <risk and mitigation>
