"""Tests for per-conversation autonomy dial + global kill switch.

TDD Phase-1: defaults are OFF/safe; set_autonomy rejects 'autonomous' (Phase-3 only)
and unknown modes — never silently accepts.
"""
import os
import tempfile

from jl import db


def _c():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c = db.connect(p)
    db.init_db(c)
    return c, p


def test_autonomy_default_off_and_set():
    c, p = _c()
    try:
        assert db.get_autonomy(c, 1) == "off"            # 默认关(安全)
        db.set_autonomy(c, 1, "observe"); assert db.get_autonomy(c, 1) == "observe"
        db.set_autonomy(c, 1, "supervised"); assert db.get_autonomy(c, 1) == "supervised"
    finally:
        c.close(); os.unlink(p)


def test_autonomy_rejects_autonomous_in_v1():
    c, p = _c()
    try:
        # v1 不接受 autonomous 挡(Phase 3 才开) → 应拒绝或回落 off,不得静默接受
        db.set_autonomy(c, 1, "autonomous")
        assert db.get_autonomy(c, 1) in ("off",)   # 拒绝→保持 off
    finally:
        c.close(); os.unlink(p)


def test_killswitch_default_off():
    c, p = _c()
    try:
        assert db.killswitch_on(c) is False
        db.set_killswitch(c, True); assert db.killswitch_on(c) is True
        db.set_killswitch(c, False); assert db.killswitch_on(c) is False
    finally:
        c.close(); os.unlink(p)
