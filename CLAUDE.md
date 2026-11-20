@AGENTS.md

## Before any `gh pr merge` (read this first)

- No `Agent review (opus): PASS <sha>` comment for the PR's **current head commit** -> **do not
  merge.** Run the review: spawn a fresh `reviewer` agent (`.claude/agents/reviewer.md`, **Opus for
  every PR** here) for the PR's first review, and **resume that same agent** (SendMessage to its agent
  id, with the new head) for every later round on the PR. A fresh one only after a second `FAIL`
  or when it cannot be resumed (the comment says so). Never review in this session.
  Running the review is the default; don't ask whether to skip it.
- Your own check, a review skill in this session, green CI and passing tests are not a `PASS`.
  "I reviewed it" in chat is not one either: the owner can merge the PR themselves on GitHub.
- Any push after a `PASS` invalidates it. A merge-only update of `main` with no hand-resolved
  conflict is covered by the Architect's merge check (AGENTS.md review gate, item 3); any other
  push needs a fresh (delta) review.
- Merge only with `gh pr merge <n> --squash --match-head-commit <reviewed-sha>`. The squash title
  is the PR title (no `--subject`). The branch need not be up to date with `main`.
- Never `--auto`, never `--admin`, never an approving review, never a ruleset change, never a
  comment starting `Human review:`.

## Code structure and token economy

1. Small, cohesive modules: a new source file has one clear responsibility and stays under about
   300 lines. Never split a cohesive function artificially to meet the cap.
2. Split on touch: if you must change a non-test source file already over 300 lines, move one
   cohesive part into its own module in the same PR, so the big file ends with a net change of 0
   lines or fewer. Tests and generated or data files are exempt.
3. Pure core first: keep rules and calculations apart from side effects (network, Redis, disk). A
   pure module never imports I/O.
4. Token-frugal checks: never read a huge file whole when grep or a line range answers the
   question. During work run only the test files that cover what you changed (CI runs the rest).

## Commits

- Conventional Commits 1.0.0 (see AGENTS.md). No `Co-Authored-By` trailer by hand; append only the
  attribution trailers the session requires.
- Never commit `.env`, tokens, keys or anything under `secrets/`.

## Plan

spotdown is classbeat's music-analysis service. The frozen plan is summarised in
[docs/integration-plan.md](docs/integration-plan.md); no phase is implemented until the owner
authorizes it. No agent calls YouTube, Spotify, SwedeBeat or MOWL, ever.
