"""Edge-collector push: run a local adapter, normalize, POST to a remote AMR /api/ingest."""
from __future__ import annotations

import json
import urllib.request
from dataclasses import asdict


def build_payload(adapter, *, account_id, label="", self_id=""):
    """Pure: adapter.pull_new(None) -> the /api/ingest JSON payload."""
    convs = []
    for conv, msgs in adapter.pull_new(None):
        convs.append({"conv": asdict(conv), "msgs": [asdict(m) for m in msgs]})
    account = {"account_id": account_id, "platform": adapter.platform, "label": label}
    if self_id:
        account["self_id"] = self_id
    return {"account": account, "conversations": convs}


def push(remote, token, payload, timeout=60):
    """POST payload to <remote>/api/ingest. Returns the parsed JSON response."""
    url = remote.rstrip("/") + "/api/ingest"
    if token:
        url += "?token=" + token
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))
