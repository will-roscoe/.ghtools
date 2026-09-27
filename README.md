# ghtools

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

## Commands

| Command | Does |
|---|---|
| `ghtools init` / `deinit` | set up / undo ghtools in the repo you're in |
| `ghtools doctor` | check settings, stub, secrets, rulesets, Pages |
| `ghtools version next --explain` | what merging now would release, and why |
| `ghtools resync` | after a push: fetch tags, fast-forward, reinstall an editable install |
| `ghtools hooks install` | warn-only pre-push hook running the configured gates |

## Versions

Repos pin `pipeline.yml@v1`, a tag moved to each new 1.x release. Breaking changes start `v2`.
Pin a full commit SHA instead of `v1` to opt out of automatic updates.

## Licence

MIT. Files ghtools writes into other repos are also MIT-0, so they carry no notice requirement.
