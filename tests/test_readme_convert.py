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
