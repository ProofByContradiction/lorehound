---
name: ship-a-change
description: >-
  The repeatable workflow for shipping a code change to the Lorehound repo:
  branch → validate → open a PR → wait for CI → merge → clean up. Use for any
  non-trivial change that should land on main. Encodes the repo's conventions
  (pre-commit hooks, no co-author trailer, ask-before-PR/merge) and — importantly —
  the branch-protection GOTCHAS that bite if you merge too fast or delete a branch
  early.
metadata:
  project: lorehound
  version: "1.0.0"
---

# Shipping a change to Lorehound

Repo facts: default branch **main**; remote **origin** (`ProofByContradiction/lorehound`).
A **pre-commit hook** runs a security audit (`pip check` + `pip-audit`) and `ruff` on
every commit. CI on each PR runs **audit + ruff + unittest** (each twice: push +
pull_request triggers). **main is branch-protected**: a PR must be *up to date with
main* and have *passing checks* to merge.

## The loop

1. **Branch off main.** `git checkout -b feature/<slug>` (or `chore/…`, `fix/…`).
   Never commit straight to main.
2. **Make the change.** Match surrounding code style/comment density.
3. **Validate before committing** — this is the real gate (see `onboard-source-book`
   for the cache method):
   - Run the change across the cached corpus and **spot-check samples**, not just
     counts, for anything touching extraction/parsing/routing.
   - `.venv/bin/python -m unittest discover -s tests -p "test_*.py"` (stdlib unittest,
     NOT pytest). Add tests for the new behaviour.
   - `.venv/bin/ruff check <files>`.
4. **Commit.** The pre-commit hook re-runs audit + ruff. **No Anthropic `Co-Authored-By`
   trailer** (see `[[no-anthropic-coauthor-trailer]]`). Write a real body: what changed,
   why, and the validation result.
5. **Ask before opening the PR** unless the user already gave a standing directive
   (see `[[ask-before-opening-or-merging-prs]]`). Then `git push -u origin <branch>` and
   `gh pr create` with a structured body (What / Fix / Validation / Left-open).
6. **WAIT for CI to go green before merging.** Poll
   `gh pr view <n> --json mergeable,mergeStateStatus` until `mergeStateStatus` is
   **CLEAN** (not BLOCKED). Only then `gh pr merge <n> --merge` (merge-commit style,
   matching the repo). Ask before merging unless told otherwise.
7. **Update + clean up:** `git checkout main && git pull --ff-only origin main`, then
   delete the branch **local and remote** (`git branch -D <b>` + `git push origin
   --delete <b>`). Confirm: `gh pr list --state open` and a quick `grep` that the change
   is on main.

## Gotchas (all hit for real — don't repeat them)

- **Don't merge too fast.** Right after you push (including after merging main into the
  branch), CI re-runs. Merging before it finishes → `mergeStateStatus: BLOCKED` /
  "base branch policy prohibits the merge". Wait for CLEAN. `sleep 20-30` then re-check;
  a bare `sleep 3` isn't enough.
- **Don't delete a branch before its PR has merged.** Deleting the remote branch
  **auto-closes the open PR** (GitHub). Recovery: the commits survive locally
  (`git cat-file -t <sha>`), so `git checkout -b <new> main && git cherry-pick <sha>`,
  push, open a fresh PR. But just don't delete early.
- **Merging PR A can un-ready PR B.** Once A lands, B is no longer up to date with main,
  so its merge is blocked. Fix: `git checkout B && git merge main && git push`, wait for
  CI, then merge B. (Order-independent PRs on different files still each need this.)
- **`git status` collapses `.claude/`** to `?? .claude/` even when only `.claude/skills/`
  is trackable — trust `git check-ignore <path>` and confirm the staged set with
  `git diff --cached --name-only`, not the summary.

## Reindex / deploy note

Most changes here are **index-time code** (parser, `classify_table`, card building,
commands) → they take effect on a **bot restart** (the startup reindex re-runs the
index-time logic on the existing cache with the new code); no re-extraction. Only a
`MD_VERSION`/`TABLE_VERSION` bump needs the standalone re-extraction (`python -m
lorehound.index --force`, ~30 min). Extraction-cache data changes hot-reload a running
bot; **code changes need a restart** (see `[[deploy-version-bump-needs-bot-restart]]`).
Validate against the cache here; the live effect wants a real reindex.
