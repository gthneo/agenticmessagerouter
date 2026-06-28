"""Guard: the INDEX_HTML <script> must be valid JavaScript.

Why this exists: INDEX_HTML is a NON-raw triple-quoted Python string, so any
backslash escape written for JS (`\\n`, `\\\\`, ...) is silently rewritten by
Python at definition time. A `\\n` meant as a JS escape becomes a real newline and
can break a regex literal — which makes the WHOLE <script> fail to parse, so every
handler goes undefined. Python tests can't see this (they don't run JS). This test
shells `node --check` over each extracted <script> so such a regression fails CI.

Skips cleanly when node is unavailable (stdlib-only ethos: no hard test dependency).
"""
import re
import shutil
import subprocess
import tempfile

import pytest

from jl import web

_HAS_NODE = shutil.which("node") is not None


def _scripts(html):
    return re.findall(r"<script>(.*?)</script>", html, flags=re.DOTALL)


@pytest.mark.skipif(not _HAS_NODE, reason="node not available; JS syntax guard skipped")
def test_index_html_scripts_are_valid_js():
    scripts = _scripts(web.INDEX_HTML)
    assert scripts, "expected at least one <script> block in INDEX_HTML"
    for i, src in enumerate(scripts):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=True) as f:
            f.write(src)
            f.flush()
            proc = subprocess.run(["node", "--check", f.name],
                                  capture_output=True, text=True)
        assert proc.returncode == 0, (
            f"<script> block #{i} is not valid JavaScript "
            f"(likely a Python-string-mangled escape in non-raw INDEX_HTML):\n"
            f"{proc.stderr}")
