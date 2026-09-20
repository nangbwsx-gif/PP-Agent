"""DETERMINISTIC EVAL — the dashboard's JavaScript actually parses.

Written the moment it was needed. A `title=` attribute added to a button
contained the word waku wrapped in backticks, inside a template literal:

    title="... the `waku` partition is never named ..."

The backticks closed the template literal early and the file stopped parsing.
Not the button, not the panel — the WHOLE file. Every view compare.js renders
went blank, and the only symptom was a page that looked like it had not
reloaded. It cost a full round of "is the browser caching?" before anyone
checked the console.

Python has ruff and 580 tests standing between a typo and a broken dashboard.
The JavaScript, which is most of what a user actually touches, had nothing.
`node --check` is a syntax check and no more — it will not catch a wrong
selector or a bad fetch — but this class of bug turns the entire page off,
which is the loudest possible failure for the cheapest possible test.

Skips when node is absent rather than failing: a contributor without node
should still get a green suite, and CI has it.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest

JS_DIR = pathlib.Path(__file__).resolve().parents[2] / "waku" / "ops" / "static" / "js"
STATIC_DIR = JS_DIR.parent
# lang/ holds the translation tables, and they can take the whole page down just
# as easily as any other script: a half-width quote inside a Chinese string
# closed it early and the entire dashboard silently fell back to English, with
# the only clue in the browser console. Hence two globs, not just js/.
SCRIPTS = sorted(STATIC_DIR.glob("js/*.js")) + sorted(STATIC_DIR.glob("lang/*.js"))


def test_there_are_scripts_to_check():
    """A guard whose glob silently matches nothing passes forever and protects
    nothing. If the frontend moves, this fails and says so."""
    assert SCRIPTS, f"no .js found under {STATIC_DIR} — did the frontend move?"


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
@pytest.mark.parametrize("script", SCRIPTS, ids=[s.name for s in SCRIPTS])
def test_every_dashboard_script_parses(script: pathlib.Path):
    result = subprocess.run(  # noqa: S603 — fixed argv, path from our own glob
        [shutil.which("node"), "--check", str(script)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, (
        f"{script.name} does not parse — the dashboard will render nothing.\n"
        f"{result.stderr.strip()}"
    )


def test_no_local_variable_shadows_the_translation_function():
    """A local variable named `t` takes out the whole view.

    t(key, "English") is a global. Declare a local `t` in the same scope —
    `const t = d.tools` in the Tools view, `function dbTable(t)` — and every
    t(...) call in that scope becomes a TypeError on a non-function, so the
    view renders nothing and the only clue is in the browser console. That is
    exactly how the Tools and Database pages went down on 2026-09-19.

    A text check, deliberately: node --check sees valid syntax here, and the
    bug only exists at runtime.
    """
    offenders = []
    for script in SCRIPTS:
        lines = script.read_text(encoding="utf-8").split("\n")
        for i, line in enumerate(lines):
            if not re.search(r"\b(?:const|let|var)\s+t\s*=", line):
                continue
            scope = "\n".join(lines[i:i + 30])
            if re.search(r'[^\w.$]t\("', scope):
                offenders.append(f"{script.name}:{i + 1}  {line.strip()[:60]}")
    assert not offenders, (
        "a local `t` shadows the global t() translator, so every t() call in "
        "that scope throws and the whole view goes blank:\n  " + "\n  ".join(offenders)
    )
