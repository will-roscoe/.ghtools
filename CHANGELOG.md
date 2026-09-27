# Changelog

All notable changes to ghtools are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions are computed from
Conventional Commits by ghtools itself.

## [Unreleased]

## [0.4.0] - 2026-09-27

### Features

- **subprojects**: add [[subprojects]] settings with dependency validation
- **subprojects**: select the subprojects a change touches and build the CI matrix
- **subprojects**: per-subproject install, setup and test commands
- **subprojects**: detect in-tree subprojects and submodules
- **subprojects**: status row, submodule pointer status and doctor checks

### Bug fixes

- **subprojects**: detect umbrellas and checked-out submodules correctly, with one .gitmodules parser
- **subprojects**: legs install their test extras, needed siblings and pytest; setup after install
- **status**: subprojects keep their latest result across partial runs

### CI

- **subprojects**: per-change matrix, subproject legs and a tests-passed check

### Style

- **tests**: format the subproject workflow test

## [0.3.0] - 2026-09-27

### Features

- **readme**: port the rST-to-Markdown converter, stricter and with alerts and numbered lists
- **readme**: named README blocks from a single source, with sync --check/--write and a gate
- **readme**: init migrates SYNC markers to ghtools:sync blocks

### Bug fixes

- **readme**: fail loudly on rST outside the subset instead of dropping or mangling it
- **readme**: clear errors for missing files, bounded heading offsets, every SYNC block detected

### Documentation

- **readme**: list the status and readme sync commands

## [0.2.0] - 2026-09-27

### Features

- **status**: add the [status] settings section
- **status**: build per-source status fragments from CI, docs and project data
- **status**: render flat badges locally
- **status**: lay out and render the status card from fragments
- **status**: render the full set of status-branch files
- **status**: publish to an orphan branch with plumbing, a lease and retries
- **status**: add ghtools status leg/collect/set/publish/render/url
- **status**: init points READMEs at the status branch; doctor checks it

### Bug fixes

- **status**: never replace the default branch or any branch that isn't a status branch
- **status**: show only released versions, and never fail CI while recording status
- **status**: report docs coverage only when the docs-coverage gate measured it
- **init**: rewrite whole badge URLs and publish every badge the README uses
- **status**: republish when settings change, and report non-race push rejections at once
- **status**: keep card text inside its card, and escape everything but the logo
- **status**: sort any Python version string, and show gates that never ran as unknown
- **status**: name fragments by their source, and inline only logos inside the repo
- **status**: fall back to the GitHub repository description on the card
- **deinit**: restore the README init rewrote, unless it was edited since

### CI

- **status**: record legs and docs, publish the status branch after each default-branch run

## [0.1.0] - 2026-09-27

### Features

- add the ghtools package skeleton and CLI entry point
- **version**: port Conventional Commits to SemVer logic with directives
- **git**: add git helpers with SemVer-only release tag sorting
- **changelog**: port changelog finalisation, merge same-named sections, keep CRLF
- **config**: add the ghtools.toml schema, validation and config commands
- **version**: read and write pyproject and manifest versions, keeping line endings
- **release**: decide, prepare, notes and build steps with version and release commands
- **ci**: add gates runner, CI test commands, path filter and docs commands
- **init**: detect a repo's ghtools settings with evidence for each
- **init**: snapshot and restore .github for init and deinit
- **init**: add ghtools init with archive, workflow classification and the stub
- **init**: add ghtools deinit and archive prune
- **doctor**: check settings, stub contract, secrets, rulesets and Pages
- **local**: add resync and a warn-only pre-push hook installer

### Bug fixes

- **init**: ask the remote for its default branch and show notes in dry runs
- **ci**: install editable by default so path coverage collects data
- **release**: fail on a failed prepare, never rebase into a release, push only after a good build
- **changelog**: never finalize a version that already has a section
- **init**: deinit refuses to overwrite untracked files that differ from the archive
- **init**: re-runs read archived workflows and never drop existing settings
- **ci**: HACS CI uses the newest Python from ci.python instead of a fixed 3.13
- **config**: reject empty matrices and settings that name missing paths
- **cli**: report errors as ::error annotations inside GitHub Actions
- **init**: detect docs install, prebuild and strictness; keep replaced workflows' make checks as gates

### CI

- add the setup-ghtools action and the python, HACS and lint CI workflows
- add the docs and release workflows
- add the pipeline entry workflow that consumer stubs call
- run ghtools's own CI and releases through its pipeline
- **python**: run tests and report coverage even when a gate fails

