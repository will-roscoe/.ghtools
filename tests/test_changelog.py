"""Tests for ghtools.changelog (ported from protonfs tests/test_changelog_finalize.py)."""

from __future__ import annotations

from datetime import date

from ghtools.changelog import finalize_changelog, release_section, render_commit_sections

_DATE = date(2026, 8, 1)

_WITH_ENTRIES = """\
# Changelog

## [Unreleased]

### Added

- feat: something new (#99)

## [0.17.0] - 2026-07-16

### Added

- stuff (#61)

[Unreleased]: https://github.com/will-roscoe/protonfs/compare/v0.17.0...HEAD
[0.17.0]: https://github.com/will-roscoe/protonfs/compare/v0.16.0...v0.17.0
"""

_EMPTY_UNRELEASED = """\
# Changelog

## [Unreleased]

## [0.17.0] - 2026-07-16

### Added

- stuff (#61)

[Unreleased]: https://github.com/will-roscoe/protonfs/compare/v0.17.0...HEAD
[0.17.0]: https://github.com/will-roscoe/protonfs/compare/v0.16.0...v0.17.0
"""

_WHITESPACE_ONLY_UNRELEASED = """\
# Changelog

## [Unreleased]


## [0.17.0] - 2026-07-16

Stuff.
"""

_NO_UNRELEASED_SECTION = """\
# Changelog

## [0.17.0] - 2026-07-16

Stuff.
"""


class TestFinalizeChangelog:
    def test_noop_when_unreleased_empty(self):
        new_text, changed = finalize_changelog(_EMPTY_UNRELEASED, "0.18.0", _DATE)
        assert changed is False
        assert new_text == _EMPTY_UNRELEASED

    def test_noop_when_unreleased_whitespace_only(self):
        new_text, changed = finalize_changelog(_WHITESPACE_ONLY_UNRELEASED, "0.18.0", _DATE)
        assert changed is False
        assert new_text == _WHITESPACE_ONLY_UNRELEASED

    def test_noop_when_no_unreleased_header(self):
        new_text, changed = finalize_changelog(_NO_UNRELEASED_SECTION, "0.18.0", _DATE)
        assert changed is False
        assert new_text == _NO_UNRELEASED_SECTION

    def test_renames_section_and_leaves_fresh_unreleased(self):
        new_text, changed = finalize_changelog(_WITH_ENTRIES, "0.18.0", _DATE)
        assert changed is True
        assert "## [Unreleased]\n\n## [0.18.0] - 2026-08-01" in new_text
        # Fresh Unreleased is empty: immediately followed by the new version header.
        assert new_text.index("## [Unreleased]") < new_text.index("## [0.18.0]")

    def test_preserves_finalized_entry_content(self):
        new_text, _ = finalize_changelog(_WITH_ENTRIES, "0.18.0", _DATE)
        assert "- feat: something new (#99)" in new_text
        # Old version section untouched.
        assert "## [0.17.0] - 2026-07-16" in new_text
        assert "- stuff (#61)" in new_text

    def test_updates_reference_links(self):
        new_text, _ = finalize_changelog(_WITH_ENTRIES, "0.18.0", _DATE)
        assert (
            "[Unreleased]: https://github.com/will-roscoe/protonfs/compare/v0.18.0...HEAD"
            in new_text
        )
        assert (
            "[0.18.0]: https://github.com/will-roscoe/protonfs/compare/v0.17.0...v0.18.0"
            in new_text
        )
        # Old Unreleased link line (pointing at v0.17.0...HEAD) is gone.
        assert "compare/v0.17.0...HEAD" not in new_text

    def test_accepts_v_prefixed_version(self):
        new_text, changed = finalize_changelog(_WITH_ENTRIES, "v0.18.0", _DATE)
        # The function does not strip a leading v itself -- callers (the CLI) do.
        assert changed is True
        assert "## [v0.18.0] - 2026-08-01" in new_text

    def test_missing_link_block_still_finalizes_section(self):
        text_no_links = _WITH_ENTRIES.split("[Unreleased]:")[0]
        new_text, changed = finalize_changelog(text_no_links, "0.18.0", _DATE)
        assert changed is True
        assert "## [0.18.0] - 2026-08-01" in new_text


class TestPrereleaseVersions:
    """Pre-release versions (v1.1.0-alpha[.n], -beta, -rc) flow through finalize:
    the section header takes the version verbatim, and the Unreleased compare link
    is recognised even when the PREVIOUS tag was itself a pre-release."""

    def test_finalizes_into_prerelease_section(self):
        new_text, changed = finalize_changelog(_WITH_ENTRIES, "1.1.0-alpha.0", _DATE)
        assert changed is True
        assert "## [1.1.0-alpha.0] - 2026-08-01" in new_text
        assert (
            "[Unreleased]: https://github.com/will-roscoe/protonfs/compare/v1.1.0-alpha.0...HEAD"
            in new_text
        )

    def test_previous_tag_may_be_a_prerelease(self):
        text = _WITH_ENTRIES.replace(
            "[Unreleased]: https://github.com/will-roscoe/protonfs/compare/v0.17.0...HEAD",
            "[Unreleased]: https://github.com/will-roscoe/protonfs/compare/v1.1.0-alpha...HEAD",
        )
        new_text, changed = finalize_changelog(text, "1.1.0-beta", _DATE)
        assert changed is True
        assert (
            "[1.1.0-beta]: https://github.com/will-roscoe/protonfs/compare/v1.1.0-alpha...v1.1.0-beta"
            in new_text
        )


class TestGeneratedReleaseNotes:
    """Release notes generated from commit subjects: grouped by type, chronological
    (earliest first) within each group, `chore` and `[skip ci]` excluded."""

    def test_groups_by_type_in_section_order(self):
        out = render_commit_sections(
            [
                "docs: explain thing",
                "feat(cli): add command",
                "fix(drive): stop crash",
            ]
        )
        assert (
            out.index("### Features") < out.index("### Bug fixes") < out.index("### Documentation")
        )
        assert "- **cli**: add command" in out
        assert "- **drive**: stop crash" in out

    def test_chronological_within_group_earliest_first(self):
        out = render_commit_sections(["feat: first", "feat: second", "feat: third"])
        assert out.index("- first") < out.index("- second") < out.index("- third")

    def test_chore_and_skip_ci_and_nonconventional_excluded(self):
        out = render_commit_sections(
            [
                "chore: tidy something",
                "docs(changelog): finalize v1.0.0 [skip ci]",
                "Merge pull request #5 from x/y",
                "feat: kept",
            ]
        )
        assert out == "### Features\n\n- kept"

    def test_finalize_appends_generated_after_handwritten(self):
        new_text, changed = finalize_changelog(
            _WITH_ENTRIES, "0.18.0", _DATE, commit_subjects=["feat(cli): add command"]
        )
        assert changed is True
        section = new_text.split("## [0.18.0] - 2026-08-01", 1)[1].split("## [", 1)[0]
        assert "### Features" in section
        assert "- **cli**: add command" in section

    def test_generated_notes_alone_make_empty_unreleased_finalizable(self):
        # The v0.18-v0.24 gap: nobody hand-wrote Unreleased entries, so releases
        # shipped without notes. Generated notes now fill that hole.
        empty = _WITH_ENTRIES.replace(
            _WITH_ENTRIES.split("## [0.17.0]", 1)[0], "# Changelog\n\n## [Unreleased]\n\n"
        )
        new_text, changed = finalize_changelog(
            empty, "0.18.0", _DATE, commit_subjects=["fix: real fix"]
        )
        assert changed is True
        assert "### Bug fixes" in new_text

    def test_no_handwritten_and_no_generated_stays_unchanged(self):
        empty = "# Changelog\n\n## [Unreleased]\n\n## [0.1.0] - 2026-01-01\n\n- x\n"
        new_text, changed = finalize_changelog(
            empty, "0.2.0", _DATE, commit_subjects=["chore: only housekeeping"]
        )
        assert changed is False


_SAME_HEADING = """\
# Changelog

## [Unreleased]

### Bug fixes

- **status**: hand-written note

## [2.2.0] - 2026-09-23

- older
"""


def test_generated_notes_merge_into_matching_handwritten_heading():
    new_text, changed = finalize_changelog(
        _SAME_HEADING, "2.2.1", date(2026, 9, 24), ["fix(status): report remote-only again (#160)"]
    )
    assert changed
    section = new_text.split("## [2.2.1] - 2026-09-24", 1)[1].split("## [", 1)[0]
    assert section.count("### Bug fixes") == 1
    assert "- **status**: hand-written note" in section
    assert "- **status**: report remote-only again (#160)" in section


def test_crlf_line_endings_are_preserved():
    crlf = _SAME_HEADING.replace("\n", "\r\n")
    new_text, changed = finalize_changelog(crlf, "2.2.1", date(2026, 9, 24), ["feat: x"])
    assert changed
    assert "\r\n" in new_text
    assert "\n" not in new_text.replace("\r\n", "")


def test_unchanged_text_is_returned_byte_for_byte():
    crlf = "# Changelog\r\n\r\n## [Unreleased]\r\n\r\n## [1.0.0] - 2026-01-01\r\n"
    assert finalize_changelog(crlf, "1.0.1", date(2026, 9, 1), []) == (crlf, False)


def test_release_section_extracts_body_without_link_definitions():
    text = (
        "# Changelog\n\n## [Unreleased]\n\n## [1.2.0] - 2026-09-01\n\n### Features\n\n- a\n\n"
        "[Unreleased]: https://github.com/o/r/compare/v1.2.0...HEAD\n"
        "[1.2.0]: https://github.com/o/r/compare/v1.1.0...v1.2.0\n"
    )
    assert release_section(text, "1.2.0") == "### Features\n\n- a"
    assert release_section(text, "v1.2.0") == "### Features\n\n- a"
    assert release_section(text, "9.9.9") == ""
