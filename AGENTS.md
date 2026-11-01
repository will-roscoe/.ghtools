# ghtools: guide for agents

ghtools is shared CI, release, docs and status tooling for will-roscoe's repositories. A repo holds a
small `.github/ghtools.toml` and a generated workflow stub that calls `pipeline.yml@v1` here, so a
change to ghtools reaches every repo on its next run. Read this when asked to adopt ghtools in a
repo, configure its docs, cut a release, or change ghtools itself. The key reference is
`SCHEMA` and `TABLE_ARRAYS` in `src/ghtools/config.py`; `ghtools config check` validates a file.

## Adopting ghtools in a repo

```bash
pipx install git+https://github.com/will-roscoe/.ghtools@v1
cd path/to/repo
ghtools init --dry-run     # what it detects and would change
ghtools init               # writes .github/ghtools.toml and .github/workflows/ghtools.yml
git diff                   # review, commit, push
ghtools doctor             # settings, stub, secrets, rulesets, Pages (needs `gh` logged in)
```

- `init` archives the old `.github/` to `.github/archive/pre-ghtools-<date>/` and removes the
  workflows ghtools replaces. `ghtools deinit` undoes it. Options: `--yes`, `--keep-old`,
  `--archive {snapshot,none}`, `--write` (overwrite an existing `ghtools.toml`), `--ref REF`,
  `--community`.
- The stub pins `pipeline.yml@v1` (a tag moved to each 1.x release). `ghtools stub` prints the stub for
  the current settings keeping its pin; `ghtools stub --write` writes it. Pin a full SHA to opt out of
  updates. `ghtools sync OWNER/NAME... | --all` opens stub-update pull requests in other repos.
- `doctor` without `gh` logged in skips the GitHub-side checks and says so.

## Keys that matter

Unset keys take the default. Unknown keys are an error that lists the allowed ones.

| Key | Default | Meaning |
|---|---|---|
| `branch` | `"main"` | the default branch releases and status run on |
| `profile` | `"python"` | `python`, `hacs`, `lint` or `umbrella` |
| `version.source` | `"tag"` | where the current version lives: `tag`, `pyproject`, `manifest` (needs `version.manifest`) |
| `version.bump` | `"write"` | `write` commits the new version into the file; `require` demands it already be there |
| `version.zero-major-breaking` | `"minor"` | in 0.x a breaking change bumps `minor` or `major` |
| `release.enabled` | `true` | release at all |
| `release.publish` | `["github"]` | any of `github`, `github-zip` (needs `release.zip`), `pypi` |
| `release.commit` | `true` | write a `chore(release): <tag> [skip ci]` commit; `false` needs `version.source = "tag"` or `bump = "require"` |
| `release.changelog` | `"CHANGELOG.md"` | changelog finalised at release |
| `release.root` | `true` | `false` stops the repo releasing as `v{x}`; only released components release |
| `ci.python`, `ci.runners` | `["3.12"]`, `["ubuntu-latest"]` | test matrix; a leg with no python build for its runner moves to an OS version that has one or is dropped with a warning |
| `ci.install`, `ci.test` | `"-e .[dev]"`, `"pytest -q"` | install and test commands |
| `ci.paths` | `["**.py", "pyproject.toml"]` | a push touching none of these skips CI |
| `ci.skip-pr-heads` | `[]` | head branches whose PRs skip CI (see release flow) |
| `ci.reuse-pr-results` | `false` | skip CI on the push that merges a PR whose tree already passed |
| `ci.select`, `ci.select-full-on` | none, `[]` | pytest markers left out when a change cannot affect them; events that run everything |
| `ci.accept-local-tested` | `false` | reuse accepts a record made by `ghtools ci test --record` |
| `ci.gates` | `["ruff", "ruff-format"]` | lint gates; custom ones go in `[[ci.gate]]` |
| `docs.enabled`, `docs.pages` | `false`, `false` | build Sphinx docs; deploy them to Pages |
| `docs.dir`, `docs.install` | `"docs"`, `".[docs]"` | Sphinx source dir (needs `conf.py`), pip install |
| `docs.apidoc`, `docs.prebuild`, `docs.apt`, `docs.strict`, `docs.paths` | `""`, `[]`, `[]`, `true`, `[]` | see Docs |
| `status.enabled` | `false` | publish the status card |
| `status.branch` | `"ghtools-status"` | branch the card is published to; must not be the default branch |
| `status.layout` | `"single"` | `"components"` for the per-component card |
| `status.card`, `status.badges`, `status.rows`, `status.links`, `status.logo` | see `config.py` | what the card shows |

Coverage (`[ci.coverage]`: `package`, `floor`, `codecov`, `uploads-per-day`), `[directives]`,
`[[readme.block]]` and `[[subprojects]]` are documented in the README and `config.py`.

## Release flow

- Open PRs into a long-lived `next` branch and merge them there. When `next` is ready, open one
  `next` -> default branch PR. Releasing happens on the push that merges it.
- Set `ci.skip-pr-heads = ["next"]` so that PR skips CI. It skips only when the `next` tree already has a
  `refs/ghtools/tested/<tree>` record, which a passing PR run into `next` writes (so `ci.reuse-pr-results`
  is not required for this).
- Conventional commits decide the bump: `feat` is minor, `fix` and `perf` are patch, a `!` or
  `BREAKING CHANGE` is major (minor in 0.x with the default). `docs`, `test`, `chore` etc. release
  nothing. Use the types honestly, since the subjects become the changelog. Squash-merge bodies
  are scanned too, so do not quote a `feat:` line at the start of a line in a PR body.
  `+:major`, `+:minor` etc. alone on a commit message line force a bump.
- `ghtools version next --explain` says what merging now would release and why.
- The release commit is `chore(release): <tag> [skip ci]`; it is a `chore`, so it never triggers
  another release.
- A tag pushed without its GitHub release (an interrupted run) is resumed by the next run, but only
  when the tag is annotated exactly `Release <tag>`. Lightweight tags and tags made by hand are never
  resumed.
- `ghtools resync` after the release fetches tags and fast-forwards your checkout.

## Docs

`docs.enabled = true` builds Sphinx from `docs.dir` (default `docs/`, which must contain `conf.py`)
with `docs.install` (default `.[docs]`, so list the extras in `pyproject.toml`) and
`sphinx-build -b html -W --keep-going` (`docs.strict = false` drops `-W`). ghtools installs only
what `docs.install` names, so that extra must list `sphinx`, the theme and every extension `conf.py`
loads. ghtools imposes no particular extension.

- **Pages:** `docs.pages = true` deploys the built site from the default branch. Enable Pages on the
  repo first: Settings, Pages, Source: GitHub Actions. `ghtools doctor` warns when its build type is
  not `workflow`. Until then the deploy cannot publish.
- **`docs.paths`** (default empty, docs build every run): path globs; when set, the docs job runs only
  if the change touches one of them, ghtools's own settings or the stub. A skipped docs job counts
  as passing and the published site stays as it was. List the sources that autodoc imports.
- **`docs.prebuild`**: shell commands run (bash, `pipefail`) before the Sphinx build, e.g. to generate
  sources. `docs.apt` lists apt packages installed first.
- **`docs.apidoc`**: a package path; `sphinx-apidoc` writes it to `<docs.dir>/api/<package name>/`
  before the build. Add the generated `modules` page to a toctree.
- **Per component:** `apidoc = ["path/to/pkg"]` on a released `[[subprojects]]` row writes
  `<docs.dir>/api/<component>/` (`<component>/<package dir name>/` when it lists several; two packages
  with the same directory name are a config error). `docs-paths` on the row adds globs whose change
  rebuilds the docs.

## Test selection and tested records

- A tested record is the ref `refs/ghtools/tested/<tree>`, written by a passing same-repo PR run (or
  locally, below). The push to the default branch reuses it only when the tree is identical, the
  record passed, and it covers every leg the push would run. A different tree (base moved, release
  commit), a fork PR, a `[ci]` directive or any doubt runs everything.
- `[[ci.select]]` rules (`marker`, `changed` globs) end `ci test` with `-m "not <marker>"` when no
  changed file matches. No rules, an unknown base, a computed merge base equal to HEAD, or an event in
  `ci.select-full-on` deselects nothing. The deselect is appended as a final `-m`, and-joined with any
  existing one.
- A record stores the selection it ran. In a repo with rules, it covers a run only if it made a claim
  and left out nothing this run wants (a full run, or one that deselected no more than this run does).
  A record with no selection claim counts as a full run only in a repo with no rules.
- `ci.test` must be a single pytest command when rules exist; `&&`, `||`, `;` or `|` in it is a config
  error. Move a compound command into a script.
- `ghtools ci test --record [--base REV]` runs the tests locally and records a clean tree as tested
  (base defaults to `origin`). It refuses `--subproject` and `--deselect` (it tests every
  subproject), and never replaces a record CI made: it prints a notice and exits 0. To redo one, drop it
  with `ghtools ci drop-tested`. Reuse accepts a local record only with `ci.accept-local-tested = true`.
- Gitignored files are not part of the tree, so a record does not check them.

```toml
[ci]
reuse-pr-results = true
skip-pr-heads = ["next"]
select-full-on = ["push"]      # every push runs everything
accept-local-tested = true

[[ci.select]]
marker = "tui"
changed = ["src/sph/phantom_live/**"]
```

## Components

A component is an in-tree `[[subprojects]]` row with `release = true`. It versions, tags and releases
on its own while the repo can keep or drop its own `v{x}` releases.

```toml
release.root = false           # optional: only components release  (in [release])

[[subprojects]]
name = "cdn"
path = "src/cdn"
release = true                 # opt in
version-file = "src/cdn/pyproject.toml"   # default <path>/pyproject.toml; .toml or a JSON manifest
changelog = "src/cdn/CHANGELOG.md"        # default <path>/CHANGELOG.md
tag = "{name}/{version}"       # default; only {name} and {version} allowed
needs = ["common"]             # other subprojects it depends on (transitive; cycles are errors)
apidoc = ["src/cdn/cdn"]
docs-paths = ["docs/cdn/**"]
tests = "src/cdn/tests"        # pytest path (default: path)
package = "cdn"                # coverage target

[[subprojects]]
name = "site"
path = "src/site"
release = true
test-command = "npm test"      # runs verbatim instead of pytest
install = []
```

Other row keys: `kind` (`in-tree` or `submodule`; submodules never release here), `install`, `setup`.

- **Tags** are `{name}/{version}` (e.g. `cdn/0.4.1`). The root's version is the newest `vX.Y.Z` tag, so
  component tags never count toward it, and a template that differs from a name's plain tag is never
  mistaken for another component's.
- **Decision:** commits since a component's latest tag count only if they touch its `path` or the path
  of anything in its `needs` (transitively). Types and bump rules are as for the root. The first release
  of an untagged component counts from the first commit.
- **Downgrade guard:** if the computed version is lower than the one in the component's version file (an
  untagged component starts from 0.0.0), nothing is released and the reason reads "tag <name>/<ver>
  first". Tag the current version, then continue. A missing or unreadable version file applies no guard.
- **Backfilling** past release points: create **lightweight** tags (`git tag cdn/0.3.0 <sha>`). Never
  annotate them `Release <tag>`, or the next run would resume them and publish old releases. Push them
  before the first component release.
- **`test-command` rows:** the row's test leg runs the command from the repo root. No `pytest` or
  `pytest-cov` is installed unless the row's `install` lists them (an empty `install` installs
  nothing). No junit or coverage files are produced unless the command writes them. The leg still runs
  `actions/setup-python`, which is harmless for non-Python code. Selection never applies to it: it runs
  in full.
- **`release-components` job** (runs on the default branch after tests and lint pass, when any component
  is released, after the root `release` job): decides, then writes version files and changelogs,
  makes **one commit** for all of them (`chore(release): cdn 0.4.1, site 1.0.0 [skip ci]`, plus an
  annotated tag `Release <tag>` each), and pushes the branch and every tag in **one atomic push**. If the branch moved
  since the run started it releases nothing and says so; the next run releases those commits
  together. Resumed tags (annotated, no GitHub release yet) skip the commit. Then it creates one
  GitHub release per component (`<name> <version>`, notes from its changelog, prerelease when the
  version has a `-`).
- Locally: `ghtools release components decide` prints each component's decision and reason;
  `prepare --decisions JSON` writes files for a decision list as given (the `components` output of
  `decide --github-output`), so the job never decides twice; `notes NAME VERSION` prints a changelog
  section. `ghtools version next --component NAME` gives one component's next version. Run a dry-run
  (`workflow_dispatch` with `dry-run`, the default) or a scratch-repo run before relying on a new setup.

## Status card

`status.enabled = true` publishes `status.svg` and badges to `status.branch`; the first default-branch run
creates the branch, and a ruleset must not block force-pushes to it. `ghtools status render` previews
it locally and `ghtools status url` prints the README URL.

`status.layout = "components"` replaces the quality cards with blocks:

- A **Repository** block: version (latest `v` tag), lint and docs build results.
- One block per released component: its name, current version (its latest tag), and the last test
  result of its test leg. A component never tagged shows "not released".
- Results of a component a run did not touch are **carried over** from the previous CI fragment, so
  each block shows that component's latest run.
- After upgrading from a ghtools without components, a card drawn from a project fragment written before
  the upgrade has no component versions, so every block says "not released" for that one run; the next
  default-branch run rewrites the fragment and fixes it.

## Pitfalls

- **Pushing `.github/workflows/*` over HTTPS needs a token with `workflow` scope.** Use SSH, or the
  push is rejected. Applies to `init`, `stub --write` and `sync` output alike.
- **Rulesets:** release commits push straight to the default branch. If a ruleset protects it, add
  GitHub Actions to the bypass list, or set `release.commit = false` (with `version.source = "tag"`).
  A ruleset that blocks force-pushes to the status branch breaks status publishing; `doctor` warns on both.
- **Prerelease flag:** the root job marks a release a prerelease when its *tag* contains a `-`
  (`v1.2.0-rc.1`, as intended), so any hyphen in a root tag counts. The components job tests the
  *version*, so a hyphen in a component's `tag` template (`my-lib/{version}`) does not.
- **Secrets** `doctor` checks: `CODECOV_TOKEN` when `ci.coverage.codecov` is on (default; uploads are
  skipped without it), `DEPS_TOKEN` when `ci.private-deps` is on. For `pypi`, the trusted publisher
  must name workflow `ghtools.yml` and environment `pypi`; `doctor` cannot check it.
- **Codecov quota:** a run uploads from one leg, only when code under `ci.paths` changed, at most
  `ci.coverage.uploads-per-day` (default 1) per day, per subproject.
- **`[skip ci]`** in a commit message skips the pipeline; ghtools adds it to its release commits. CI directives (`[ci]`, `[test]`, `[lint]`, ...) are off unless
  `[directives] enabled = true`. Quote one in backticks to mention it without acting.
- **`ci.test` with `ci.coverage.package`** must be pytest; the command ghtools runs adds the junit and
  coverage flags itself.
- Hand-edited stubs drift: re-render with `ghtools stub --write` instead of editing.

## Changing ghtools itself

Most repos depend on ghtools at `@v1`, so a change must not alter what a repo already sees.

- **Additive only.** New behaviour goes behind a new key that is off by default. Existing defaults,
  outputs, tag formats, status model and card, and workflow contracts (stub, job names, outputs) do not
  change without the owner's explicit permission. Existing functions gain only keyword parameters whose
  defaults keep today's result.
- Register keys in `SCHEMA` or `TABLE_ARRAYS` in `config.py`, validate them in `_cross_checks` or
  `_check_subprojects`, and raise `ConfigError`, `PreconditionError` or `CheckFailed` from
  `errors.py`.
- `tests/test_compat_golden.py` renders every committed `.github/ghtools.toml` fixture (ghtools's own and
  copies of other repos') and compares it with `tests/fixtures/golden`. It must pass unchanged.
  `scripts/update_compat_golden.py` regenerates the goldens; run it only for a deliberate, approved
  change, never to make a failing test pass.
- Before a PR: `.venv/bin/python -m pytest -q`, `.venv/bin/actionlint`, `ruff check .` and
  `ruff format --check .`.
- Target PRs at `next`. Commit subjects (`feat`, `fix`, `docs`, `test`, ...) drive ghtools's own
  releases, so use them honestly. Stage explicit paths.
