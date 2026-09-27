# Changelog

All notable changes to ghtools are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions are computed from
Conventional Commits by ghtools itself.

## [Unreleased]

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

