from __future__ import annotations

from ghtools import config, docs


def test_build_commands_with_apidoc_and_strict():
    cfg = config.from_dict({"docs": {"enabled": True, "dir": "docs", "apidoc": "src/protonfs"}})
    assert docs.build_commands(cfg) == [
        [
            "sphinx-apidoc",
            "-f",
            "-o",
            "docs/api/protonfs",
            "src/protonfs",
            "--separate",
            "--module-first",
            "-q",
        ],
        ["sphinx-build", "-b", "html", "-W", "--keep-going", "docs", "docs/_build/html"],
    ]
    assert docs.html_dir(cfg) == "docs/_build/html"


def test_build_commands_not_strict_no_apidoc():
    cfg = config.from_dict({"docs": {"enabled": True, "dir": "doc", "strict": False}})
    assert docs.build_commands(cfg) == [["sphinx-build", "-b", "html", "doc", "doc/_build/html"]]
