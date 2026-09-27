"""README blocks generated from a single source each (spec §7)."""

from __future__ import annotations

import re
from pathlib import Path

from .config import Config
from .errors import GhtoolsError
from .status.publish import repo_slug, status_url


class ReadmeError(GhtoolsError):
    """A fragment uses rST outside the supported subset, or README markers are wrong."""


# Non-greedy so a literal may itself contain a backtick (``:ref:`x```); protonfs's `[^`]+` couldn't.
_LITERAL = re.compile(r"``(.+?)``")
_LINK = re.compile(r"`([^`<]+?) <([^>]+)>`_")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ROLE = re.compile(r":([a-z][\w-]*):`")
_DIRECTIVE = re.compile(r"^\.\. ([a-z][\w-]*)::\s*(.*)$")
_UNDERLINE = re.compile(r"^([=\-~])\1{2,}\s*$")
_NUMBERED = re.compile(r"^(?:\d+|#)\. ")
_ADMONITIONS = {
    "note": "NOTE",
    "tip": "TIP",
    "important": "IMPORTANT",
    "warning": "WARNING",
    "caution": "CAUTION",
}
_LEVELS = {"=": 2, "-": 3, "~": 4}


def _inline(text: str, source: str, lineno: int) -> str:
    literals: list[str] = []

    def stash(match: re.Match[str]) -> str:
        literals.append(match.group(1))
        return f"\0{len(literals) - 1}\0"

    text = _LITERAL.sub(stash, text)
    role = _ROLE.search(text)
    if role:
        raise ReadmeError(f"{source}:{lineno}: unsupported role ':{role.group(1)}:'")
    text = _LINK.sub(r"[\1](\2)", text)
    text = _BOLD.sub(r"**\1**", text)
    return re.sub(r"\0(\d+)\0", lambda m: f"`{literals[int(m.group(1))]}`", text)


def _indented_body(lines: list[str], i: int) -> tuple[list[str], int]:
    """Collect an indented directive body starting at `i` (leading blank lines skipped)."""
    n = len(lines)
    while i < n and not lines[i].strip():
        i += 1
    body: list[str] = []
    while i < n and (lines[i].startswith("   ") or not lines[i].strip()):
        body.append(lines[i][3:] if lines[i].startswith("   ") else "")
        i += 1
    while body and not body[-1].strip():
        body.pop()
    return body, i


def rst_to_markdown(rst: str, heading_offset: int = 0, source: str = "<rst>") -> str:
    lines = rst.replace("\r\n", "\n").splitlines()
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        line, lineno = lines[i], i + 1
        if line.startswith("+-") or re.fullmatch(r"=+( +=+)+\s*", line):
            raise ReadmeError(f"{source}:{lineno}: tables are not supported")
        directive = _DIRECTIVE.match(line)
        if directive:
            name, arg = directive.group(1), directive.group(2).strip()
            if name == "code-block":
                body, i = _indented_body(lines, i + 1)
                out += [f"```{arg}", *body, "```", ""]
                continue
            if name in _ADMONITIONS:
                body, i = _indented_body(lines, i + 1)
                text = " ".join(
                    part.strip() for part in ([arg] if arg else []) + body if part.strip()
                )
                out += [f"> [!{_ADMONITIONS[name]}]", f"> {_inline(text, source, lineno)}", ""]
                continue
            raise ReadmeError(f"{source}:{lineno}: unsupported directive '{name}'")
        if line.startswith(".. ") or line == "..":  # a comment, possibly with an indented body
            i += 1
            while i < n and lines[i].startswith("   "):
                i += 1
            continue
        if i + 1 < n and line.strip() and _UNDERLINE.match(lines[i + 1]):
            level = _LEVELS[lines[i + 1][0]] + heading_offset
            out += [f"{'#' * level} {_inline(line.strip(), source, lineno)}", ""]
            i += 2
            continue
        if line.startswith("- ") or _NUMBERED.match(line):
            numbered = not line.startswith("- ")
            item = _inline(line.split(" ", 1)[1].strip(), source, lineno)
            i += 1
            while i < n and lines[i].startswith("  ") and lines[i].strip():
                item += " " + _inline(lines[i].strip(), source, i + 1)
                i += 1
            out.append(f"{'1.' if numbered else '-'} {item}")
            continue
        if not line.strip():
            if out and out[-1] != "":
                out.append("")
            i += 1
            continue
        para = [_inline(line.strip(), source, lineno)]
        i += 1
        while (
            i < n
            and lines[i].strip()
            and not lines[i].startswith(("- ", ".. "))
            and not _NUMBERED.match(lines[i])
            and not (i + 1 < n and _UNDERLINE.match(lines[i + 1]))
        ):
            para.append(_inline(lines[i].strip(), source, i + 1))
            i += 1
        out += [" ".join(para), ""]
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


def markers(name: str, source: str) -> tuple[str, str]:
    return (
        f"<!-- ghtools:sync {name} START — generated from {source}, do not edit here -->",
        f"<!-- ghtools:sync {name} END -->",
    )


def _content(block: dict, root: Path, cfg: Config) -> tuple[str, str]:
    if block["kind"] == "status":
        slug = repo_slug(root)
        if not slug:
            raise ReadmeError(f"{block['name']}: the status block needs a GitHub 'origin' remote")
        url = status_url(slug, cfg.get("status.branch"), "status.svg")
        return "the status branch", f'<img src="{url}" alt="{slug} status" width="900">\n'
    path = root / block["source"]
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".rst":
        return block["source"], rst_to_markdown(text, block["heading-offset"], block["source"])
    return block["source"], text.replace("\r\n", "\n").rstrip("\n") + "\n"


def render_block(block: dict, root: Path, cfg: Config) -> str:
    source, body = _content(block, Path(root), cfg)
    start, end = markers(block["name"], source)
    return f"{start}\n\n{body}\n{end}"


def _span(text: str, name: str) -> tuple[int, int]:
    starts = [
        m.start()
        for m in re.finditer(rf"<!-- ghtools:sync {re.escape(name)} START\b[^\n]*-->", text)
    ]
    ends = [m.end() for m in re.finditer(rf"<!-- ghtools:sync {re.escape(name)} END -->", text)]
    if not starts and not ends:
        start, end = markers(name, "<source>")
        raise ReadmeError(
            f"README.md has no ghtools:sync {name} markers; "
            f"add these where the block goes:\n{start}\n{end}"
        )
    if len(starts) != 1:
        raise ReadmeError(f"{name}: {len(starts)} START markers (need exactly one)")
    if len(ends) != 1:
        raise ReadmeError(f"{name}: {len(ends)} END markers (need exactly one)")
    if ends[0] < starts[0]:
        raise ReadmeError(f"{name}: END marker before START")
    return starts[0], ends[0]


def sync(root: Path, cfg: Config, write: bool) -> list[str]:
    root = Path(root)
    path = root / "README.md"
    raw = path.read_bytes().decode("utf-8")
    crlf = "\r\n" in raw
    text = raw.replace("\r\n", "\n")
    changed: list[str] = []
    for block in cfg.get("readme.block"):
        start, end = _span(text, block["name"])
        new = render_block(block, root, cfg)
        if text[start:end] != new:
            changed.append(block["name"])
            text = text[:start] + new + text[end:]
    if write and changed:
        path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))
    return changed
