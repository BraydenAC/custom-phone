---
name: feature-workflow
description: The git development process for this repo - start a feature from a user request (or a roadmap item the user picks), branch, plan it with the user as chunks in dependency waves, run chunk subagents in parallel worktrees, merge wave by wave, then leak-check, push, open a PR, get it reviewed by the pr-reviewer subagent, recap and hand over for the user's squash-merge. Use when starting or continuing feature work, merging a wave, finishing a branch, or when the user asks how branches, chunks, waves, reviews or PRs work here.
---

# Feature workflow

You orchestrate. You talk with the user, keep the plan, launch chunk agents,
merge, and run the phone checks. Chunk agents write code. Starting this
process is the user's permission to commit on feature and chunk branches,
never on `main`. Every push and every PR action still needs the user's yes.

Small work (one chunk) skips waves and chunk agents: do it on the feature
branch directly, then go from step 6.

## 0. Start clean

```sh
git status --porcelain            # must be empty, otherwise stop and ask
git switch main && git pull --ff-only
```

If the pull isn't a fast-forward, stop and ask. Never stash, reset or merge
`main` silently.

## 1. Pick the feature

The feature comes from the user's request, or from `ROADMAP.md` if the user
asks what's next and picks one. Suggest from the roadmap, never pick for the
user. Restate the objective in one or two sentences and get a yes.

## 2. Branch and record the objective

```sh
git switch -c feature/<slug>      # short, lowercase, hyphens: feature/lid-sounds
```

Create `plans/<slug>.md` (template below) with the objective, and commit it.
The plan is your source of truth: update it at every step, so a compacted
conversation can pick up from it.

## 3. Decompose with the user

Break the feature into chunks, and agree on them with the user before any
code. A good chunk:

- Has one clear result, can be built and tested on its own, and leaves the
  full suite green when merged alone.
- Touches one or two areas (`.claude/rules/`). Read the `connects:` lists of
  those areas to spot dependencies now rather than mid-chunk.
- Doesn't need the phone, a kernel build or the user to be finished. Mark
  any chunk that needs hardware: its code is done by an agent, and its phone
  check is yours after the merge.

Pick a model per chunk. The default is `sonnet`, which is enough for a
well-specified chunk with clear tests. Mark a chunk `opus` when a subtle bug
would be expensive and tests can't catch everything: the security boundary
(`phone-link`, the guard, privilege), concurrency, or unfamiliar hardware
such as device-tree bring-up.

Then group the chunks into waves. Wave 1 chunks depend on nothing. A wave N
chunk depends only on chunks in earlier waves. Two chunks that edit the same
lines belong in different waves (or should be one chunk).

## 4. Run a wave

With the feature branch checked out (worktrees start from it), launch one
chunk agent per chunk, all in the same message so they run in parallel.
Each is an Agent tool call with:
- subagent type `feature-chunk-developer`;
- `isolation: "worktree"`;
- `model: "opus"` for chunks marked opus.

Each agent creates `chunk/<slug>/<chunk>` from the feature branch.
Note that git can't have both `feature/x` and `feature/x/...`, which is why
the chunk prefix differs.

The brief each agent gets:

```
Feature: <objective>
Chunk: <id> - <one-line result>
Base branch: feature/<slug>   Work branch: chunk/<slug>/<id>
Done when: <observable result + the tests that prove it>
Areas: <rules> (read .claude/rules/<area>.md)   Don't touch: <other chunks' files>
Depends on (already merged): <chunks>
Report: done (commits, tests, notes) | blocked (needs <X>, which chunk/wave it belongs to)
```

Chunk agents fix minor problems themselves, report chunk-level issues and
carry on, and stop with `blocked` on breaking problems.
- **`blocked` on a dependency:** move the chunk (and anything that depends on
  it) to a later wave in the plan, tell the user, and carry on with the rest
  of the wave.
- **`blocked` on a design question or a bad brief:** settle it with the user,
  fix the brief, and relaunch the chunk.
- **Came back stuck on Sonnet:** rerun it with `model: "opus"`.

## 5. Merge the wave

On the feature branch, one chunk at a time:

```sh
git merge --no-ff chunk/<slug>/<id> -m "Merge chunk <id>: <result>"
uv run pytest -q                  # after every merge, not just at the end
```

- Resolve mechanical conflicts yourself, keeping both chunks' intent. If a
  conflict is a design choice, ask the user.
- Then trace the impact (`connects:`) of everything the wave touched.
- Act on the chunk reports. Chunk agents don't touch docs or rules, so this
  is where they get updated:
  - apply each report's "Docs/rules to update" (README, `docs/`,
    `.claude/rules/`, `ROADMAP.md`, `CLAUDE.md`, skills) and commit;
  - put each "chunk-level issue" into the plan: as a chunk in a later wave if
    it belongs in this feature, otherwise in the plan's Deferred list (which
    goes to the roadmap). Ask the user when it's unclear.
- Phone checks (wave end). Chunk agents only read from the phone:
  - deploy and check what the wave changed, following each report's
    "Phone steps" (`deploy-to-phone` skill);
  - collect anything the user must do, such as `flip build` then
    `flip sd update`, into one ask;
  - record the results in the plan.
- Clean up the wave: `git worktree remove <path>`, then
  `git branch -d chunk/<slug>/<id>` (already merged, so `-d` is safe).
- Update the plan, commit, and start the next wave.

## 6. Finish the branch

1. Run the full suite, and check docs:
   - README for user-facing changes;
   - `docs/architecture.md` and its diagrams;
   - `ROADMAP.md`: add deferred work, remove finished items;
   - rules, skills and `CLAUDE.md` if the workflow changed.
2. Leak scan the whole diff before anything is published, because the repo
   is public:

   ```sh
   git diff main...HEAD | grep -nE '\b[0-9]{15}\b|([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}|PRIVATE KEY|/home/[a-z]|@[a-z0-9-]+\.[a-z]{2,}'
   ```

   Every hit must be explained (for example a test's made-up value), or
   removed.
3. Write the final record from the plan: what changed, why, the chunks,
   discovered dependencies, hardware checks and their results, and anything
   deferred (which goes in the roadmap). Then remove the plan in a commit
   whose message is that record: `git rm plans/<slug>.md`.
4. With the user's yes, push: `git push -u origin feature/<slug>`.
5. With the user's yes, open the PR:
   `gh pr create --base main --title "<feature>" --body-file <record>`.
   Add the attribution line from the session instructions to the body.

## 7. Review, recap, hand over

1. Launch the `pr-reviewer` subagent with the PR number and the objective.
   Fix the blocking and should-fix findings on the branch (fixes are commits,
   so push again with the user's yes). Discuss the rest with the user.
2. Recap for the user:
   - the PR link;
   - what changed;
   - the review's verdict and what was fixed;
   - what needs their hardware check;
   - what went to the roadmap.
3. The user merges with "squash and merge", keeping the PR description as the
   commit body, so the commit on `main` is the full record.
4. Ask the user to run `/compact`. You can't run it yourself. The plan's
   content is in the PR, so nothing is lost.

## Plan template (`plans/<slug>.md`)

```markdown
# <Feature>

Objective: <one or two sentences, agreed with the user>
Branch: feature/<slug>   Started: <date>

## Chunks
| id | result | wave | depends on | areas | model | needs phone | status |
|---|---|---|---|---|---|---|---|
| a-... | ... | 1 | - | phone-overlay | sonnet | yes | todo |

## Log
- <date> wave 1 merged; chunk c blocked on b (moved to wave 2)

## Hardware checks
- <what was checked on the phone, by whom, result>

## Deferred
- <goes to ROADMAP.md at the end>
```
