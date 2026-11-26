# AGENTS.md: how agents build spotdown

spotdown is an independent music-analysis service: audio in, structured analysis out. It is
also the analysis service of classbeat. The owner (`@magpern`) makes product decisions and gives
external approvals; agents do everything else.

**Governance is classbeat's workflow, unchanged.** The frozen plan is
[classbeat `docs/plans/spotdown-integration.md`](https://github.com/magpern/classbeat/blob/main/docs/plans/spotdown-integration.md)
(§9 "Governance", classbeat roadmap Amendment 31). spotdown's own summary of that plan is
[docs/integration-plan.md](docs/integration-plan.md). Where this file, the summary and the classbeat plan
differ, the classbeat plan wins. The owner authorized the phases of the plan on 2026-11-20; deploying and the owner decisions it lists stay the owner's.

## Roles

| Role | Model | Owns | Must not |
|---|---|---|---|
| Architect / Orchestrator | Opus | The milestone, its PR split and acceptance criteria, dependencies, scheduling, merge order, ADRs | Normally write production code |
| Developer | Sonnet; Opus for security-critical PRs (below) and for the fix round after a second `FAIL` (PR loop) | One PR in its own branch and worktree: code, tests, CI fixes, **all** reviewer findings | Change architecture or acceptance criteria without the Architect |
| Explorer | Haiku | Bounded work: searches, evidence, fixtures, docs, extra tests | Own security, auth, isolation, crypto or billing decisions |
| Reviewer | **Opus for every PR except a docs-only one** (owner decision for this programme, plan §9; stricter than classbeat's Sonnet review of non-security PRs); only a PR that is docs-only by the one definition in review gate item 2 may be reviewed by Sonnet (owner, 2026-11-20). A fresh context for a PR's first review, **resumed** (same agent) for later rounds; shares no context with the Architect or Developer | The diff, the acceptance criteria, tests, CI output, plan compliance: `PASS` or `FAIL` with findings | Fix its own findings; review an ADR it wrote |

Model *classes*, not versions: use the current generation of each tier. A model substitution
never widens a role's authority. Agent definitions live in `.claude/agents/`.

**Security-critical PRs get an Opus Developer**: secret and key handling, egress and any code that
decides which hosts the service may call, authentication of callers, and anything that writes to
an account outside this repository. The issue says so in its "Owner:" line. Tooling (CI, scripts,
docs) is not security-critical by default, except that a change to how secrets are mounted,
exposed or read gets an Opus Developer.

## Unit of work: one issue, one PR

- The Architect splits a milestone into issues, one per PR, each with scope, acceptance criteria
  and dependencies ("Depends on #n").
- A PR closes one issue (`Closes #n` in the body) and states its scope, its acceptance criteria and
  how each was tested.

## PR loop

```
issue -> Developer (+ Explorer) in its own worktree -> PR -> CI
      -> fresh Reviewer (Opus; Sonnet only if docs-only, review gate item 2) -> PASS -> agent merges
                              -> FAIL -> Developer fixes every blocking finding -> CI -> resumed Reviewer (delta)
```

If the same blocking finding survives three cycles, the Architect re-assesses the root cause,
revises the issue, and the loop restarts with a fresh Reviewer.

**Owner, 2026-11-20 (classbeat roadmap Amendment 32):** after a **second `FAIL` on the same PR**, the
next fix round has an **Opus Developer**: a fresh Opus Developer given the PR's context (the branch,
the `FAIL` comments and the earlier rounds), or the same Developer agent resumed only if it already
runs on Opus. A Sonnet Developer is never "switched" to Opus; it is replaced. Before handing back any fix, every Developer runs **every** finding through
a class-wide self-check: grep for the same defect pattern elsewhere, make each call inside an
error-swallowing `except` raise and check the outcome, show that each gate can fail, and verify every
claim against the code. The Orchestrator keeps going
without routine human input; it escalates only genuine blockers (see "Escalation").

## Review gate (mandatory for every PR an agent merges)

1. The PR has a Reviewer `PASS` for its **current head commit**, recorded on the PR as a comment
   that starts `Agent review (opus): PASS <exact head SHA>` followed by the non-blocking findings.
   A `FAIL` is recorded the same way: `Agent review (opus): FAIL <exact head SHA>`. For a PR that
   is **docs-only** (the term is defined only in item 2) the comment may instead start
   `Agent review (sonnet): PASS <exact head SHA>` (or `FAIL`) and contains the line `docs-only: yes`.
2. The Reviewer is **Opus for every PR except a docs-only one**, and a fresh context for the PR's
   first review. Never the Developer's context, never the Architect's. *(Owner, 2026-11-20, classbeat
   roadmap Amendment 32: a docs-only PR may be reviewed by Sonnet.)*

   **Definition of docs-only (the only one; every other file points here and restates none of it).**
   A PR is docs-only only when **all** of the following hold for its **whole diff against `main` at
   the reviewed head** (not the latest delta):
   1. every changed path (added, modified, deleted, or either side of a rename) is a `*.md` prose
      file outside `tests/` and outside every directory whose name starts with a dot (`.claude/`,
      `.github/` and the like), read by no code, test, CI job or tool. Any
      other file (for example `quality/quality_gates.json`, `Dockerfile`, `requirements*`,
      `commitlint.config.cjs`, `.env.example`, `.gitattributes`, anything under `.claude/` or
      `.github/`) fails this;
   2. it changes no file named `AGENTS.md` or `CLAUDE.md` in any directory, not
      `docs/integration-plan.md` and no file under `docs/adr/` (agent instructions, governance, the
      plan and decisions are always Opus);
   3. it changes no rule, in any file, about roles and models, the PR loop, the review gate, merging,
      the safety envelope, security or escalation, and adds or changes no security design;
   4. it is not security-critical (Roles above): its issue's "Owner:" line does not ask for an Opus
      Developer, and it touches nothing about secret or key handling, egress, caller authentication
      or writes to an account outside this repository.

   A PR that fails any condition is not docs-only: it is reviewed by Opus, and **the merge gate
   rejects a Sonnet `PASS` on it.** A Sonnet Reviewer checks every condition first; if any fails or
   is in doubt it records no verdict and reports that the PR needs Opus. Its comment contains the
   line `docs-only: yes` and says it checked conditions 1 to 4. Before merging on a Sonnet `PASS`
   the Architect checks all four again on the head's whole diff (`gh pr diff <n> --name-only` for 1
   and 2, the changed text for 3 and 4) and rejects the `PASS` if any fails. If a later push makes a
   docs-only PR fail the definition, the next round is a fresh Opus Reviewer (a Sonnet Reviewer
   cannot be resumed as Opus; the comment says so). When in doubt, Opus.
3. **A new head SHA needs a new review**:
   - *Merge-only update:* the only change since the reviewed head is a merge of `main` with no
     hand-resolved conflict (`git diff <reviewed>..<head>` equals `main`'s own change) and CI is
     green. The Architect records `Agent review (merge check): PASS <head SHA> (carries <reviewed SHA>)`.
     If `main` changed a module the PR changes or calls, or a conflict was resolved by hand, the
     Reviewer checks the merge (scoped review).
   - *Fix round after a `FAIL`:* a **delta review** of the diff since the last reviewed head,
     verifying each earlier finding and whatever the delta touches.
   - *Reviewer reuse:* every later round on the same PR **resumes the same Reviewer agent** (send
     it the new head SHA) instead of spawning a new one. A fresh Reviewer is used only after a
     second `FAIL` on the same PR (the Architect first re-assesses the design), or when the
     Reviewer cannot be resumed; the review comment then says so.
   - Reviewers rely on CI for the full suite and run only the targeted tests of what changed.
     Mutation checks only for the guarantees a PR adds or a fix changes, at most about 8 per review.
4. Not a `PASS`: the implementing session's own check, a review skill run in the same context,
   green CI, passing tests, or "it's fine" in chat. A human saying so in chat does not replace the
   review for an agent merge either.
5. Agents record verdicts only as comments. They never submit approving reviews and never write a
   comment starting `Human review:`.
6. A `FAIL` is never overridden by an agent.

**A human may merge any PR on GitHub regardless of the verdict.** That is the owner's decision,
never an agent's: an agent never merges without the gate, even when told to skip it.

## Merging

Agents merge only with:

```bash
gh pr merge <n> --squash --match-head-commit <reviewed-sha>
```

and only when the PR's **current head** has green required checks (`ci` and `pr-title`), the `PASS`
is for that SHA (`Agent review (opus): PASS <sha>`; an `Agent review (sonnet): PASS <sha>` only if
the PR is docs-only by the definition in review gate item 2, all four conditions checked on the
head's whole diff) and no blocking comment is open. `--match-head-commit` makes GitHub refuse the
merge if the head moved after the review. The squash commit's title is the PR title (with
` (#<n>)`), so there is no `--subject`, and the PR title must be the Conventional Commits subject.
**Never `--auto`, never `--admin`, never an approving review, never a change to the ruleset** to get
a merge through. The ruleset on `main` (PR required, required checks `ci` and `pr-title`, squash
only) is set by the owner; agents never change rulesets.

## Branches, worktrees and commits

- Every task is a feature branch `<issue>-<slug>` and a PR. One writing agent per branch.
  Parallel developers each use their own git worktree (`git worktree add ../spotdown-wt/<issue>-<slug>`).
- **Conventional Commits 1.0.0** for commits, PR titles and squash subjects: `feat(analyze): ...`,
  `fix(sources): ...`, `refactor: ...`, `test: ...`, `ci: ...`, `docs: ...`, `build: ...`, `chore: ...`.
  Breaking changes use `!` or a `BREAKING CHANGE:` footer. CI checks the PR title with commitlint
  (`commitlint.config.cjs`, `pr-title` check).
- Nobody adds a `Co-Authored-By` trailer by hand. The attribution trailers that the agent runtime
  requires for a session are appended exactly as given. PR descriptions end with
  `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

## Safety envelope

**Agents may, without asking:** create branches, PRs and issues in this repository; review and
merge per the gate; run local checks and tests.

**Read-only calls to external services (owner, 2026-11-25, [#111](https://github.com/magpern/spotdown/issues/111);
widens [#85](https://github.com/magpern/spotdown/issues/85)).** The owner: "Change repo rules. You
can call read-only. Agent can do stuff read-only on external servers, unless explicitly told to do
write". So an agent may make live **read-only** requests to external services by hand, to check
behaviour against real data: YouTube through yt-dlp (metadata, search, and fetching media to check
a download), Spotify (public pages and, with owner-provided credentials, the read-only Web API with
a client-credentials token), SwedeBeat, MOWL and any other service.

- **Writes need the owner's explicit request for that specific write**: anything that creates,
  changes or deletes data on an account or service, uploads, posts or publishes. Agents never
  create apps, accounts or keys on a provider.
- These are manual checks by an agent, never part of the automated suite: tests and CI never touch
  the network (Security rules), and tests fake every provider.
- Spotify specifics carry over from #85: the Web API only with owner-provided credentials, only
  `GET` requests to `api.spotify.com` with a client-credentials token (plus that token's one
  `POST accounts.spotify.com/api/token`), never user-authorized OAuth scopes.
- Fetched media is kept only for the check and deleted after it. No credentials, tokens or cookies
  are printed, logged or committed.
- On this point the owner's direction (2026-11-25, #111) applies in place of the classbeat plan's
  wording ("no ... real YouTube, SwedeBeat, MOWL or Spotify call by any agent", its §6 "agents and
  CI never call YouTube"); the matching classbeat plan amendment is the owner's and still pending.

**Only with the owner's authorization for that specific action:** deploying,
DNS and TLS, paid services, provider dashboards, emails to real people, anything that writes to a
real account, contacting providers.

## Security rules

- Never commit, print or log secrets: API keys, client secrets, tokens, cookies, signed URLs.
  `.env` is never committed; `.env.example` holds placeholders only.
- Tests and CI never touch the network or real accounts. The test suite enforces it: an autouse
  fixture (`tests/conftest.py`, `tests/netguard.py`) makes every non-loopback `connect` and name
  lookup raise before anything is sent. CI has no network access except installing packages.
- Never weaken a test, lint or type check to make CI pass.

## Decisions and escalation

Routine technical decisions never go to the owner. When a fact is unknown, the Architect picks the
safest reversible default, isolates it behind an interface or setting, and records it in
`docs/adr/NNNN-title.md`. Escalate to the owner only for external permissions or agreements,
credentials agents cannot provision safely, product decisions, authorizing a phase of the plan,
and live-account tests.

## Developer commands

Python 3.11 (the Dockerfile's version). From the repository root:

```bash
python -m venv .venv && . .venv/bin/activate        # Git Bash: . .venv/Scripts/activate
pip install -r requirements-dev.txt
ruff check . && ruff format --check . && pyright
pytest tests/path/to/test_file.py                    # targeted; CI runs the whole suite
```

Locally run lint, types and the targeted tests only; CI runs the full suite.

## Durable state

The repository and GitHub are the source of truth, never chat history: the classbeat plan,
[docs/integration-plan.md](docs/integration-plan.md), `docs/adr/`, issues and PR descriptions. To
resume after a context reset: read `CLAUDE.md`, `AGENTS.md` and the plan, inspect open PRs, their CI
and review comments, then continue with the next unblocked issue.
