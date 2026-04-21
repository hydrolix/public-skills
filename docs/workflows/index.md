# Workflow Index

This index records repo-local workflow expectations for Hydrolix public skills.
Use it together with `README.md` and any active `AGENTS.md` instructions.

## Before Editing

Run these checks before changing files:

```bash
git status --short --untracked-files=all
git branch --show-current
git rev-parse --git-dir
```

If linked worktrees may matter, also run:

```bash
git worktree list --porcelain
```

Do not commit on `main` unless the user explicitly asks for that.

## Validation

The maintained validation contract is
[`docs/reference/validation-contracts.md`](../reference/validation-contracts.md).
Use that file for canonical command forms and warning policy.

Current canonical commands:

```bash
uv run python -m unittest discover -s tests
uv run python scripts/validate-skill-examples.py .
```

Avoid raw `python` or `python3` in committed workflow docs and Ralph task
`test_command` values unless a task is documenting a host that does not provide
`uv`.

## Ralph Loop Plans

Use `docs/plans/<slug>/` for Ralph Loop implementation packages when no stricter
project convention exists:

```text
docs/plans/<slug>/
├── plan.md
└── tasks/
    ├── 01-short-name.md
    └── 02-short-name.md
```

`plan.md` is the single source of truth for task status and must contain the
runner-visible checklist. Task files live under `tasks/`; do not add a mirrored
`tasks/IMPLEMENTATION_PLAN.md`.

Each task file must include YAML frontmatter with a runnable `test_command`.
Use [`docs/templates/ralph-task.md`](../templates/ralph-task.md) for new task
files.

Keep tasks small and linear. Split a task when it would modify more than three
files, combine unrelated work, or require a broad final validation before any
targeted check can catch the intended behavior.

When creating or reviewing a Ralph Loop package:

1. Run the baseline repository validation commands above.
2. Confirm every checklist entry in `plan.md` points to an existing numbered
   task file under `tasks/`.
3. Confirm every numbered task file starts with YAML frontmatter and includes a
   `test_command`.
4. Confirm `depends_on` values refer to earlier task files or are empty.
5. Run each distinct task `test_command` pattern when feasible, or explain why a
   command was not run.

## Existing Validator Warnings

If a validator reports pre-existing warnings outside the touched files, report
them in the final handoff instead of fixing them opportunistically. Fix warning
noise only when it is in scope for the requested work, affects files you
changed, or blocks the requested validation.

## Review-To-Fix Iteration

When turning review findings into fixes, keep a small remaining-concerns list
and update it after each finding class is resolved. Run the targeted regression
check for that finding before moving on, then run the canonical validation when
the batch is complete.

## Reviewing Untracked Files

`git diff` and `git diff --stat` do not show untracked files. If
`git status --short` shows an untracked directory, expand it before final
review:

```bash
git status --short --untracked-files=all
find docs -type f | sort
```

Use the narrowest `find` path that matches the work. In final summaries, name
the created files or the root directory that contains them.

## Final Response Checklist

- State the process or deliverable files that changed.
- State the exact validation commands run.
- Separate pre-existing warnings from newly introduced warnings.
- Mention any command that could not be run and why.
