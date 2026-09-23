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


def test_translated_text_is_never_used_as_an_identifier():
    """t() 只翻译给人看的文字。一旦它出现在比较条件、CSS 类名或查找键的位置，
    功能就断了 —— 而且从中文界面上看不出原因，因为显示出来的文字恰好是对的。

    真实事故：批量翻译把 "connected" 无差别包成 t(...)，于是

        state === t("conn.state.connected", "connected")     // 拿中文"已连接"比英文状态值
        className: t("conn.state.connected", "connected")    // CSS 类名变成了中文

    第一行让所有连接卡片掉到兜底分支、统统显示"未配置"；第二行让状态圆点失去
    颜色。服务端数据一直是对的，所以只有看渲染结果才看得出来。

    这是文本检查，和遮蔽检测同样的道理：语法完全合法，问题只在运行时。
    """
    forbidden = {
        r"={2,3}\s*t\(": "比较条件",
        r"className:\s*t\(": "CSS 类名",
        # 用作索引：something[t(...)]。不能写成 `\[\s*t\(` —— 那会误报所有
        # 数组字面量，而 uiTable([t("tbl.subject",...), ...]) 里的译文正是
        # 该翻的表头文字。
        r"[\w\)\]]\s*\[\s*t\(": "对象索引",
        r"case\s+t\(": "switch 分支",
        r"class=\"\$\{t\(": "HTML class 属性",
    }
    offenders = []
    for script in SCRIPTS:
        src = script.read_text(encoding="utf-8")
        for pattern, why in forbidden.items():
            for match in re.finditer(pattern, src):
                line = src[:match.start()].count("\n") + 1
                offenders.append(f"{script.name}:{line}  把 t() 用作了{why}")
    assert not offenders, (
        "译文只能出现在给人看的位置，不能当标识符用：\n  " + "\n  ".join(offenders)
    )
