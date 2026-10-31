<img src="https://github.com/will-roscoe/.ghtools/raw/ghtools-status/status.svg" alt="ghtools status" width="900">

Shared GitHub tooling for will-roscoe's repositories. CI, Conventional-Commit releases, docs
and the status card are defined once here. Each repo *references* them, so a
change here reaches every repo on its next run without a commit to that repo.

## Using it in a repo

```bash
pipx install git+https://github.com/will-roscoe/.ghtools@v1
cd path/to/repo
ghtools init --dry-run   # see what it detects and would change
ghtools init             # writes .github/ghtools.toml + .github/workflows/ghtools.yml
git diff                 # review, then commit and push
ghtools doctor           # check the GitHub side (secrets, rulesets, Pages)
```

`init` archives the old `.github/` to `.github/archive/pre-ghtools-<date>/` before removing
the workflows ghtools replaces. `ghtools deinit` undoes it.

## Configuration

`.github/ghtools.toml` holds only what differs between repos. Every key and its default is in
`src/ghtools/config.py` (`SCHEMA`); `ghtools config check` validates the file, and
`ghtools init` writes each detected value with a comment naming the evidence.

## Runner resolution

`ghtools ci matrix` pairs each `ci.python` with `ci.runners[0]` (and the newest python with the
other runners), then checks every leg against the Python builds `actions/setup-python` can
download. A runner label is parsed to OS, version and architecture (`ubuntu-latest` is looked up
in `src/ghtools/data/runners.json`, the one hand-kept fact, since GitHub moves the `-latest`
aliases). If `src/ghtools/data/python_builds.json` has a build for that OS version, the label is
kept. Otherwise the leg moves to the newest OS version of the same OS and architecture that has
one, under an explicit label (`3.9` on `ubuntu-latest` runs on `ubuntu-24.04`; on
`ubuntu-24.04-arm` it stays arm). With no build on any version the leg is dropped with a
`::warning::skipped: no python X.Y build for os/arch`. `ghtools config check` prints the same
warnings.

`rules` in `runners.json` cover what the manifest cannot say. A rule applies to legs matching
`match` (`python`, `os`, `arch`; versions compare numerically, `3.10 > 3.9`) and requires the
runner's OS version to satisfy `require.version`:

```json
{"match": {"python": ">=3.15", "os": "darwin"}, "require": {"version": "<=15"}}
```

Refresh the build snapshot with `python scripts/refresh_python_manifest.py` (fetches
`actions/python-versions`; the matrix itself never touches the network).

## Commands

| Command | Does |
|---|---|
| `ghtools init` / `deinit` | set up / undo ghtools in the repo you're in |
| `ghtools doctor` | check settings, stub, secrets, rulesets, Pages |
| `ghtools init --community` | also add issue forms, a PR template, CONTRIBUTING and SECURITY where missing |
| `ghtools sync OWNER/NAME… \| --all [--dry-run]` | open a pull request updating each repo's stub when the stub contract changes |
| `ghtools version next --explain` | what merging now would release, and why |
| `ghtools resync` | after a push: fetch tags, fast-forward, reinstall an editable install |
| `ghtools hooks install` | warn-only pre-push hook running the configured gates |
| `ghtools status url` / `render` | the status card's README URL / a local preview of the status branch |
| `ghtools stub [--write]` | the workflow stub for the current settings, keeping its pinned ref |
| `ghtools readme sync --check` / `--write` | check or regenerate README blocks (`[[readme.block]]`) from their single source |
| `ghtools codecov --check` / `--write` | check or write `.github/codecov.yml` from `[ci.coverage]` (project may drop `threshold` points, default 2; patch is informational; `[[ci.coverage.component]]` rows become Codecov components) |

## Codecov uploads

Each upload counts against the Codecov plan's monthly allowance, so a run uploads coverage and test
results from one test leg (the first, plus one per subproject), and only when code under
`ci.paths` changed. `ci.coverage.uploads-per-day` (default 1, `0` = no limit) caps the runs that
upload per UTC day, counted separately for each subproject. The count is kept in Actions cache
entries, which are per branch: a pull request sees what the default branch uploaded today, but
not the other way round.

```toml
[ci.coverage]
uploads-per-day = 3
```

## Skipping CI on pull requests

`ci.skip-pr-heads` (default empty) lists head branches whose pull requests skip CI (tests, gates,
docker and lint legs), for a release branch whose commits were already tested as pull requests
into it. Other pull requests and all pushes are unaffected.

```toml
[ci]
skip-pr-heads = ["next"]
```

## Building docs only when relevant

`docs.paths` (default empty = docs build on every run) lists path globs, like `ci.paths`. When
set, the docs job runs only if the change touches one of them or ghtools's own settings and stub: a
pull request is compared with its base, a push with its old head. A skipped job counts as passing
for branch protection, and `docs-deploy` is skipped with it, so the published site stays as it was.
Pushes to the default branch follow the same rule, so docs are rebuilt and deployed on the push
that merges a docs-relevant change (a failed deploy is retried by the next such push or a manual
run, which has no base and always builds). Include the sources the docs import, for autodoc.

```toml
[docs]
paths = ["doc/**", "src/**.py", "pyproject.toml"]
```

## Reusing pull request test results

`ci.reuse-pr-results` (default false) lets the push that merges a pull request skip CI when the
pushed commit's *tree* already passed. A same-repo pull request run that passes records its merged
tree and its status legs as `refs/ghtools/tested/<tree>` (a ref, no branch or tag). The push to
the default branch looks its own tree up there: found, passing, and covering every leg the push
would run, then tests, lint and docker are skipped, the recorded legs feed the status card and
badges, and the record is deleted. A different tree (the base moved after the pull request ran, a
release commit on the default branch) runs everything as usual, as does a `[ci]` directive, a fork
pull request (read-only token) or any doubt. Codecov is not uploaded for a reused push. Needs
nothing beyond the stub's existing `contents: write`.

```toml
[ci]
reuse-pr-results = true
```

## Commit directives

Off by default. With `[directives] enabled = true`, tokens in the commits pushed to the default
branch act on that pipeline run, written `[name]` or `/name`:

| Token | Effect |
|---|---|
| `[ci]`, `[test]`, `[test-full]`, `[lint]`, `[coverage]` (optionally `:py`) | run CI even if no watched path changed |
| any of those with another language, e.g. `[lint:f2]` | a warning; nothing to run |
| `[docs]` | a notice: docs already build on every run (with `docs.paths` set it does not force a build) |
| a name listed in `dispatch` | start that repository workflow (`gh workflow run`) once the pipeline succeeds |

```toml
[directives]
enabled = true
dispatch = { update-todo = "update-todo.yml" }
```

A directive quoted in backticks (`` `[ci]` ``) is prose and does nothing. Release directives
(`+:major`, `+:minor`, …, alone on a line) are separate and always on. `ghtools stub --write`
adds the stub's dispatch job after you add `dispatch` entries.

## Updating the stub everywhere

The stub changes rarely (a new trigger, secret or publish job). When it does, `STUB_VERSION` goes up
and every stub is out of date. Run `ghtools sync --all --dry-run` to see which repos need it, then
`ghtools sync --all` to open one pull request per repo. Each is re-rendered from that repo's own
settings and keeps its pinned ref; repos with an open update PR are skipped.

## Versions

Repos pin `pipeline.yml@v1`, a tag moved to each new 1.x release. Breaking changes start `v2`.
Pin a full commit SHA instead of `v1` to opt out of automatic updates.

## Licence

MIT. Files ghtools writes into other repos are also MIT-0, so they carry no notice requirement.
