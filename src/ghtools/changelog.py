"""Finalize the ``[Unreleased]`` section of a Keep-a-Changelog CHANGELOG.md for a release.

Ported from protonfs's .github/scripts/finalize_changelog.py (same author, relicensed MIT).
"""

from __future__ import annotations

import re
from datetime import date

_UNRELEASED_HEADER = "## [Unreleased]"
_HEADER_RE = re.compile(r"^## \[", re.MULTILINE)
_UNRELEASED_LINK_RE = re.compile(
    r"^\[Unreleased\]: (?P<base>https://\S+?)/compare/"
    r"(?P<prev_tag>v\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)(?:\.\d+)?)?)\.\.\.HEAD$",
    re.MULTILINE,
)


# Release-notes generation from Conventional Commit subjects: section order in the
# finalized entry. `chore` is deliberately absent (housekeeping stays out of release
# notes), as is anything carrying `[skip ci]` (badge/changelog bot commits).
_SECTION_ORDER: list[tuple[str, str]] = [
    ("feat", "Features"),
    ("fix", "Bug fixes"),
    ("perf", "Performance"),
    ("revert", "Reverts"),
    ("refactor", "Refactors"),
    ("docs", "Documentation"),
    ("test", "Tests"),
    ("build", "Build"),
    ("ci", "CI"),
    ("style", "Style"),
]
_SUBJECT_RE = re.compile(r"^(?P<type>[a-zA-Z]+)(?:\((?P<scope>[^)]*)\))?!?:\s*(?P<desc>.+)$")


def render_commit_sections(subjects: list[str]) -> str:
    """Group Conventional Commit subjects (oldest first) into `### <Section>` blocks.

    Within each section commits stay chronological, earliest at the top. Subjects
    that are non-conventional, `chore`-typed, of an unknown type, or tagged
    `[skip ci]` are dropped. Returns "" when nothing survives.
    """
    titles = dict(_SECTION_ORDER)
    groups: dict[str, list[str]] = {}
    for subject in subjects:
        subject = subject.strip()
        if not subject or "[skip ci]" in subject:
            continue
        match = _SUBJECT_RE.match(subject)
        if not match:
            continue
        ctype = match.group("type").lower()
        if ctype not in titles:
            continue
        scope, desc = match.group("scope"), match.group("desc")
        line = f"- **{scope}**: {desc}" if scope else f"- {desc}"
        groups.setdefault(ctype, []).append(line)
    blocks = [
        f"### {title}\n\n" + "\n".join(groups[ctype])
        for ctype, title in _SECTION_ORDER
        if ctype in groups
    ]
    return "\n\n".join(blocks)


_SUBHEADER_RE = re.compile(r"^### (.+?)\s*$", re.MULTILINE)
_LINK_DEF_RE = re.compile(r"^\[[^\]]+\]: \S+\s*$")


def _split_sections(body: str) -> tuple[str, list[tuple[str, str]]]:
    """Split an entry body into (preamble, [(heading, content), ...]) on ``### `` lines."""
    matches = list(_SUBHEADER_RE.finditer(body))
    if not matches:
        return body.strip(), []
    sections = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections.append((match.group(1), body[match.end() : end].strip()))
    return body[: matches[0].start()].strip(), sections


def _merge_sections(handwritten: str, generated: str) -> str:
    """Merge generated ``### Type`` blocks into hand-written ones with the same heading.

    Fixes protonfs's [2.2.1] entry, which carried two "### Bug fixes" headings: one
    hand-written, one generated. Headings compare case-insensitively; hand-written
    order is kept and new headings are appended in generated order.
    """
    preamble, merged = _split_sections(handwritten)
    _, generated_sections = _split_sections(generated)
    index = {heading.lower(): i for i, (heading, _) in enumerate(merged)}
    for heading, content in generated_sections:
        i = index.get(heading.lower())
        if i is None:
            index[heading.lower()] = len(merged)
            merged.append((heading, content))
        else:
            existing_heading, existing = merged[i]
            merged[i] = (existing_heading, f"{existing}\n{content}" if existing else content)
    parts = [preamble] if preamble else []
    parts += [f"### {h}\n\n{c}" if c else f"### {h}" for h, c in merged]
    return "\n\n".join(parts)


def _finalize_lf(
    text: str,
    version: str,
    release_date: date,
    commit_subjects: list[str] | None = None,
) -> tuple[str, bool]:
    """Return ``(new_text, changed)``.

    Renames the ``## [Unreleased]`` section to ``## [<version>] - <release_date>``
    and inserts a fresh empty ``## [Unreleased]`` above it. The finalized entry is
    any hand-written Unreleased content followed by release notes generated from
    `commit_subjects` (oldest first; see `render_commit_sections`). Also rewires
    the reference-style links at the bottom of the file when present. Leaves
    `text` untouched (``changed=False``) when there is neither hand-written nor
    generated content.
    """
    start = text.find(_UNRELEASED_HEADER)
    if start == -1:
        return text, False

    body_start = start + len(_UNRELEASED_HEADER)
    next_header = _HEADER_RE.search(text, body_start)
    body_end = next_header.start() if next_header else len(text)
    body = text[body_start:body_end]

    generated = render_commit_sections(commit_subjects or [])
    if generated:
        merged = _merge_sections(body, generated) if body.strip() else generated
        body = f"\n\n{merged}\n\n"

    if not body.strip():
        return text, False

    date_str = release_date.isoformat()
    new_section = f"{_UNRELEASED_HEADER}\n\n## [{version}] - {date_str}{body}"
    new_text = text[:start] + new_section + text[body_end:]

    link_match = _UNRELEASED_LINK_RE.search(new_text)
    if link_match:
        base = link_match.group("base")
        prev_tag = link_match.group("prev_tag")
        old_line = link_match.group(0)
        new_lines = (
            f"[Unreleased]: {base}/compare/v{version}...HEAD\n"
            f"[{version}]: {base}/compare/{prev_tag}...v{version}"
        )
        new_text = new_text.replace(old_line, new_lines, 1)

    return new_text, True


def finalize_changelog(
    text: str,
    version: str,
    release_date: date,
    commit_subjects: list[str] | None = None,
) -> tuple[str, bool]:
    """Return ``(new_text, changed)``; see `_finalize_lf`. CRLF files stay CRLF.

    Like the protonfs original, a leading ``v`` is *not* stripped here (callers pass a bare
    version; the ported test ``test_accepts_v_prefixed_version`` pins this).
    """
    crlf = "\r\n" in text
    work = text.replace("\r\n", "\n") if crlf else text
    if re.search(rf"^## \[{re.escape(version)}\]", work, re.M):
        return text, False  # already finalized (a retried release): never add a second section
    new_text, changed = _finalize_lf(work, version, release_date, commit_subjects)
    if not changed:
        return text, False
    return (new_text.replace("\n", "\r\n") if crlf else new_text), True


def release_section(text: str, version: str) -> str:
    """Body of ``## [<version>] …`` without its heading or trailing link definitions."""
    text = text.replace("\r\n", "\n")
    match = re.search(rf"^## \[{re.escape(version.strip().lstrip('v'))}\][^\n]*\n", text, re.M)
    if not match:
        return ""
    nxt = _HEADER_RE.search(text, match.end())
    lines = text[match.end() : nxt.start() if nxt else len(text)].rstrip().splitlines()
    while lines and (not lines[-1].strip() or _LINK_DEF_RE.match(lines[-1])):
        lines.pop()
    return "\n".join(lines).strip()
