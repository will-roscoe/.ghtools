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


def test_prebuild_commands_run_before_sphinx():
    cfg = config.from_dict({"docs": {"enabled": True, "prebuild": ["make docs"]}})
    assert docs.build_commands(cfg)[0] == ["bash", "-o", "pipefail", "-c", "make docs"]


def _components(*rows):
    return config.from_dict({"docs": {"enabled": True, "dir": "doc"}, "subprojects": list(rows)})


def test_apidoc_per_component():
    cfg = _components(
        {"name": "cdn", "path": "src/cdn", "release": True, "apidoc": ["src/cdn/cdn"]},
        {"name": "web", "path": "src/web", "release": True, "apidoc": ["src/web/a", "src/web/b"]},
        {"name": "lib", "path": "lib", "apidoc": ["lib/lib"]},  # not released: ignored
    )
    cmds = docs.build_commands(cfg)
    outs = [(c[3], c[4]) for c in cmds if c[0] == "sphinx-apidoc"]
    assert outs == [
        ("doc/api/cdn", "src/cdn/cdn"),
        ("doc/api/web/a", "src/web/a"),
        ("doc/api/web/b", "src/web/b"),
    ]
    assert cmds[-1][0] == "sphinx-build"


def test_no_components_leaves_build_commands_unchanged():
    cfg = _components({"name": "lib", "path": "lib", "apidoc": ["lib/lib"]})
    assert docs.build_commands(cfg) == [
        ["sphinx-build", "-b", "html", "-W", "--keep-going", "doc", "doc/_build/html"]
    ]
