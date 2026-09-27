"""Tests for the rST → Markdown converter (ported from protonfs tests/test_sync_readme.py)."""

from __future__ import annotations

import pytest

from ghtools.readme import ReadmeError, rst_to_markdown


def test_section_headers_by_underline():
    out = rst_to_markdown("Title\n=====\n\nSub\n---\n\nDeep\n~~~~\n")
    assert out == "## Title\n\n### Sub\n\n#### Deep\n"


def test_heading_offset():
    assert rst_to_markdown("Title\n=====\n", heading_offset=1) == "### Title\n"


def test_inline_literal_link_bold_italic():
    out = rst_to_markdown("Use ``pip`` with `docs <https://x.org>`_ and **care** and *style*.\n")
    assert out == "Use `pip` with [docs](https://x.org) and **care** and *style*.\n"


def test_code_block_becomes_fenced():
    out = rst_to_markdown(".. code-block:: bash\n\n   pip install x\n   x --help\n\nAfter.\n")
    assert out == "```bash\npip install x\nx --help\n```\n\nAfter.\n"


def test_bullet_list_with_wrapped_continuation():
    out = rst_to_markdown("- one\n  continued\n- two\n")
    assert out == "- one continued\n- two\n"


def test_numbered_list():
    assert rst_to_markdown("1. first\n2. second\n#. third\n") == "1. first\n1. second\n1. third\n"


def test_comment_lines_are_dropped_even_when_they_mention_directives_and_roles():
    rst = (
        ".. Single source of truth, included via `.. include::` elsewhere. Do NOT use\n"
        ".. :doc:/:ref: roles here.\n"
        "Body.\n"
    )
    assert rst_to_markdown(rst) == "Body.\n"


def test_paragraph_lines_are_joined():
    assert rst_to_markdown("one\ntwo\n\nthree\n") == "one two\n\nthree\n"


def test_admonitions_become_github_alerts():
    out = rst_to_markdown(".. note::\n\n   Be careful\n   here.\n\n.. warning:: Short form.\n")
    assert out == "> [!NOTE]\n> Be careful here.\n\n> [!WARNING]\n> Short form.\n"


@pytest.mark.parametrize(
    ("rst", "message"),
    [
        (".. image:: logo.png\n", "overview.rst:1: unsupported directive 'image'"),
        ("See :ref:`install`.\n", "overview.rst:1: unsupported role ':ref:'"),
        ("+---+---+\n| a | b |\n+---+---+\n", "overview.rst:1: tables are not supported"),
    ],
)
def test_unsupported_constructs_name_the_line(rst, message):
    with pytest.raises(ReadmeError, match=message.replace(".", r"\.")):
        rst_to_markdown(rst, source="overview.rst")


def test_roles_inside_inline_literals_are_fine():
    assert rst_to_markdown("Write ``:ref:`x``` like this.\n") == "Write `:ref:`x`` like this.\n"


# Review C1: explicit markup that isn't a plain comment must never vanish silently.
def test_directive_names_are_case_insensitive():
    assert rst_to_markdown(".. Note::\n\n   Careful.\n") == "> [!NOTE]\n> Careful.\n"


@pytest.mark.parametrize(
    ("rst", "message"),
    [
        (".. Image:: logo.png\n", "unsupported directive 'image'"),
        ("..  image:: logo.png\n", "unsupported directive 'image'"),
        (".. _Foo: https://x\n\nSee `Foo`_.\n", "hyperlink targets are not supported"),
        (".. |b| image:: badge.svg\n", "substitutions are not supported"),
        (".. [1] footnote text\n", "footnotes and citations are not supported"),
    ],
)
def test_explicit_markup_is_never_dropped(rst, message):
    with pytest.raises(ReadmeError, match=r"x\.rst:1: " + message):
        rst_to_markdown(rst, source="x.rst")


def test_bare_anchor_labels_are_dropped():
    assert rst_to_markdown(".. _overview:\n\nBody.\n") == "Body.\n"


# Review I1: anything outside the subset fails loudly instead of being mangled.
@pytest.mark.parametrize(
    ("rst", "message"),
    [
        ("- a\n  - b\n", "nested lists are not supported"),
        ("- a\n\n  more\n", "indented text"),
        ("Example::\n\n   code\n", r"literal blocks \(::\) are not supported"),
        ("| a\n| b\n", "line blocks are not supported"),
        ("term\n   definition\n", "indented text"),
        ("   quoted\n", "indented text"),
        (":field: value\n", "field lists are not supported"),
        ("Title\n^^^^^\n", "heading underline '\\^' is not supported"),
        ("=====\nTitle\n=====\n", "overlined titles are not supported"),
        ("See `Foo`_ now.\n", "named hyperlink references are not supported"),
        (".. code-block:: py\n   :caption: x\n\n   code\n", "directive options are not supported"),
        (".. note::\n\n   - a\n   - b\n", "admonitions may only hold one paragraph"),
        (".. note::\n\n   one\n\n   two\n", "admonitions may only hold one paragraph"),
    ],
)
def test_unsupported_rst_fails_loudly(rst, message):
    with pytest.raises(ReadmeError, match=r"x\.rst:\d+: " + message):
        rst_to_markdown(rst, source="x.rst")


def test_star_and_plus_bullets_and_anonymous_links_convert():
    assert rst_to_markdown("* one\n+ two\n") == "- one\n- two\n"
    assert rst_to_markdown("Use `docs <https://x.org>`__.\n") == "Use [docs](https://x.org).\n"


def test_code_block_with_four_space_indent():
    assert rst_to_markdown(".. code-block:: sh\n\n    a\n      b\n") == "```sh\na\n  b\n```\n"


# Review I2: a link wrapped across lines still converts.
def test_link_wrapped_across_lines():
    rst = "Sync with the `Proton Drive\nCLI <https://x>`_ tool.\n"
    assert rst_to_markdown(rst) == "Sync with the [Proton Drive CLI](https://x) tool.\n"


def test_list_item_link_wrapped_across_lines():
    rst = "- the `Proton\n  Drive <https://x>`_ app\n"
    assert rst_to_markdown(rst) == "- the [Proton Drive](https://x) app\n"


# Review I3: a comment's whole body is dropped, however it is indented.
@pytest.mark.parametrize(
    "rst",
    [
        "..\n   Internal note\n\n   SECRET para2\n\nBody.\n",
        ".. c\n  two space\n\nBody.\n",
        ".. c\n\ttab\n\nBody.\n",
    ],
)
def test_comment_bodies_never_leak(rst):
    assert rst_to_markdown(rst) == "Body.\n"
