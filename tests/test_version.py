"""Version + capability visibility — the two-sided version handshake.

Covers: single-source version (pyproject ↔ __version__ no-drift), the CONSUMES
manifest, /api/version response, the UI badge in the rendered page, the adapter's
X-AMR-Version request header, and `jl account ls` live-probe of backend versions.
"""
from __future__ import annotations

import re
from pathlib import Path

import jl
from jl import onboard, version, web
from jl.channels import fullwechat


def _pyproject_version():
    txt = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', txt)
    assert m, "no version in pyproject.toml"
    return m.group(1)


def test_version_single_source_no_drift():
    # __version__ is THE source; pyproject must mirror it (keep them locked).
    assert jl.__version__ == version.__version__ == "0.11.0"
    assert _pyproject_version() == jl.__version__


def test_consumes_manifest_shape_and_truth():
    c = version.CONSUMES
    # the consumer truth (王总钦定): canonical YES, send-target YES, moments v1 YES,
    # group-metadata NO, control NO.
    assert c["message.canonical"] == "1"
    assert c["send-target"] == "v1"
    assert c["moments"] == "v1"          # v1 read/like consumed; v2 publish NOT
    assert c["group.canonical"] is None  # roster exposed by backend, not yet consumed
    assert c["control"] is None


def test_api_version_response():
    resp = web.api_version()
    assert resp["amr_version"] == jl.__version__
    assert resp["consumes"] == version.CONSUMES
    assert resp["consumes"]["message.canonical"] == "1"


def test_index_html_carries_version_badge():
    html = web._index_html()
    assert f"AMR v{jl.__version__}" in html
    assert "__AMR_VERSION__" not in html        # placeholder fully substituted
    assert "id=amrver" in html                  # the always-visible badge element


def test_adapter_outbound_headers_announce_amr():
    h = fullwechat._auth_headers("tok-test")
    assert h["X-AMR-Version"] == jl.__version__
    assert h["X-AMR-Consumes"] == "message.canonical/1"
    assert h["Authorization"] == "Bearer tok-test"
    # extras merge (used by the POST send path for Content-Type)
    h2 = fullwechat._auth_headers("tok", extra={"Content-Type": "application/json"})
    assert h2["Content-Type"] == "application/json"
    assert h2["X-AMR-Version"] == jl.__version__


def test_probe_backend_versions_reads_both_axes():
    def fake_fetch(host, path, token, timeout=5):
        if path == "/api/status":
            return {"version": "0.12.0"}
        if path == "/api/capabilities":
            return {"schema": "message.canonical/1"}
        raise AssertionError(path)
    pv = onboard.probe_backend_versions("http://backend.test", "tok", fetch=fake_fetch)
    assert pv == {"version": "0.12.0", "schema": "message.canonical/1"}


def test_probe_backend_versions_degrades_when_down():
    def boom(host, path, token, timeout=5):
        raise OSError("connection refused")
    pv = onboard.probe_backend_versions("http://down.test", "tok", fetch=boom)
    assert pv == {"version": "unreachable", "schema": "unreachable"}


def test_probe_backend_versions_missing_fields():
    def fake_fetch(host, path, token, timeout=5):
        return {}  # endpoint exists but exposes neither field
    pv = onboard.probe_backend_versions("http://x.test", "tok", fetch=fake_fetch)
    assert pv == {"version": "?", "schema": "?"}
