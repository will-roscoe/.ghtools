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
_LINK = re.compile(r"`([^`<]+?) <([^>]+)>`__?")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ROLE = re.compile(r":([a-z][\w-]*):`")
_REFERENCE = re.compile(r"`[^`]+`__?(?![\w`])")
_EXPLICIT = re.compile(r"^\.\.(?:\s+(.*))?$")
_DIRECTIVE = re.compile(r"([\w.:+-]+?)::(?:\s+(.*))?$")
_ADORNMENT = re.compile(r"^([!-/:-@\[-`{-~])\1{2,}\s*$")
_BULLET = re.compile(r"^[-*+] ")
_NUMBERED = re.compile(r"^(?:\d+|#)\. ")
_FIELD = re.compile(r"^:[\w .-]+:(?:\s|$)")
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
    if _REFERENCE.search(text):
        raise ReadmeError(f"{source}:{lineno}: named hyperlink references are not supported")
    text = _BOLD.sub(r"**\1**", text)
    return re.sub(r"\0(\d+)\0", lambda m: f"`{literals[int(m.group(1))]}`", text)


def _indented(line: str) -> bool:
    return bool(line) and line[0] in " \t"


def _indented_body(lines: list[str], i: int, source: str) -> tuple[list[str], int]:
    """An indented directive body from `i` (leading blanks skipped), dedented by its first line."""
    n = len(lines)
    while i < n and not lines[i].strip():
        i += 1
    if i < n and _indented(lines[i]) and _FIELD.match(lines[i].lstrip()):
        raise ReadmeError(f"{source}:{i + 1}: directive options are not supported")
    body: list[str] = []
    indent = len(lines[i]) - len(lines[i].lstrip()) if i < n and _indented(lines[i]) else 0
    while indent and i < n and (not lines[i].strip() or lines[i][:indent].strip() == ""):
        body.append(lines[i][indent:])
        i += 1
    while body and not body[-1].strip():
        body.pop()
    return body, i


def _explicit(lines: list[str], i: int, source: str, out: list[str]) -> int:
    """Handle a line starting `..`: a supported directive, an anchor, a comment, or an error."""
    lineno = i + 1
    rest = (_EXPLICIT.match(lines[i]).group(1) or "").strip()
    directive = _DIRECTIVE.match(rest)
    if directive:
        name, arg = directive.group(1).lower(), (directive.group(2) or "").strip()
        if name == "code-block":
            body, i = _indented_body(lines, i + 1, source)
            out += [f"```{arg}", *body, "```", ""]
            return i
        if name in _ADMONITIONS:
            body, i = _indented_body(lines, i + 1, source)
            parts = ([arg] if arg else []) + body
            text_lines = [p.strip() for p in parts]
            structured = any(
                _BULLET.match(t) or _NUMBERED.match(t) or t.startswith("..") for t in text_lines
            )
            if structured or "" in text_lines:
                raise ReadmeError(f"{source}:{lineno}: admonitions may only hold one paragraph")
            text = _inline(" ".join(text_lines), source, lineno)
            out += [f"> [!{_ADMONITIONS[name]}]", f"> {text}", ""]
            return i
        raise ReadmeError(f"{source}:{lineno}: unsupported directive '{name}'")
    if rest.startswith("_"):
        if re.fullmatch(r"_[^:`]+:", rest):  # a bare anchor label: no content to lose
            return i + 1
        raise ReadmeError(f"{source}:{lineno}: hyperlink targets are not supported")
    if rest.startswith("|"):
        raise ReadmeError(f"{source}:{lineno}: substitutions are not supported")
    if rest.startswith("["):
        raise ReadmeError(f"{source}:{lineno}: footnotes and citations are not supported")
    # A comment: its body is every following blank or indented line, however deep.
    i += 1
    while i < len(lines) and (not lines[i].strip() or _indented(lines[i])):
        i += 1
    return i


def _check_line(line: str, source: str, lineno: int) -> None:
    """Constructs outside the subset that would otherwise be mangled into paragraphs."""
    if line.startswith("+-") or re.fullmatch(r"=+( +=+)+\s*", line):
        raise ReadmeError(f"{source}:{lineno}: tables are not supported")
    if _indented(line) and line.strip():
        raise ReadmeError(
            f"{source}:{lineno}: indented text (block quote, definition list or nested content) "
            "is not supported"
        )
    if line.startswith("| ") or line == "|":
        raise ReadmeError(f"{source}:{lineno}: line blocks are not supported")
    if _FIELD.match(line):
        raise ReadmeError(f"{source}:{lineno}: field lists are not supported")


def rst_to_markdown(rst: str, heading_offset: int = 0, source: str = "<rst>") -> str:
    lines = rst.replace("\r\n", "\n").splitlines()
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        line, lineno = lines[i], i + 1
        if not line.strip():
            if out and out[-1] != "":
                out.append("")
            i += 1
            continue
        if _EXPLICIT.match(line):
            i = _explicit(lines, i, source, out)
            continue
        _check_line(line, source, lineno)
        if _ADORNMENT.match(line) and i + 2 < n and _ADORNMENT.match(lines[i + 2]):
            raise ReadmeError(f"{source}:{lineno}: overlined titles are not supported")
        if i + 1 < n and _ADORNMENT.match(lines[i + 1]):
            char = lines[i + 1][0]
            if char not in _LEVELS:
                raise ReadmeError(
                    f"{source}:{lineno + 1}: heading underline '{char}' is not supported "
                    "(use =, - or ~)"
                )
            out += [
                f"{'#' * (_LEVELS[char] + heading_offset)} {_inline(line.strip(), source, lineno)}",
                "",
            ]
            i += 2
            continue
        if _BULLET.match(line) or _NUMBERED.match(line):
            numbered = not _BULLET.match(line)
            words = [line.split(" ", 1)[1].strip()]
            i += 1
            while i < n and _indented(lines[i]) and lines[i].strip():
                cont = lines[i].strip()
                if _BULLET.match(cont) or _NUMBERED.match(cont):
                    raise ReadmeError(f"{source}:{i + 1}: nested lists are not supported")
                words.append(cont)
                i += 1
            out.append(f"{'1.' if numbered else '-'} {_inline(' '.join(words), source, lineno)}")
            continue
        para = [line.strip()]
        i += 1
        while i < n and lines[i].strip():
            nxt = lines[i]
            if (
                _EXPLICIT.match(nxt)
                or _BULLET.match(nxt)
                or _NUMBERED.match(nxt)
                or (i + 1 < n and _ADORNMENT.match(lines[i + 1]))
            ):
                break
            _check_line(nxt, source, i + 1)
            para.append(nxt.strip())
            i += 1
        text = " ".join(para)
        if text.rstrip().endswith("::"):
            raise ReadmeError(
                f"{source}:{lineno}: literal blocks (::) are not supported; use .. code-block::"
            )
        out += [_inline(text, source, lineno), ""]
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
