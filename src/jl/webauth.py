"""Username/password gate for the AMR web inbox — a human-friendly front door over
the existing `JL_WEB_TOKEN`.

Why this exists: the raw token is fine for Agents/ops (header/?token=), but a person
shouldn't have to paste a 32-hex token to open their inbox. So we add ONE optional
layer: a user/password that, when verified, hands the browser the real token (which it
then remembers in localStorage). No password is ever stored in plaintext — only a
salted PBKDF2-HMAC-SHA256 hash, in `~/.config/jl/web_auth.json` (0600, off-repo).

LLM-optional core (项目铁律): this is pure stdlib (`hashlib`/`hmac`/`secrets`), zero
network, zero model. If the auth file is absent the web server falls back to the token
gate — nothing breaks, the human stays in control.

The password is set by the human via `python3 -m jl.webauth` (interactive getpass) on
the host; it never travels through chat, an Agent, or any field an Agent fills in.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets

AUTH_PATH = os.path.expanduser("~/.config/jl/web_auth.json")
ITERATIONS = 200_000


def _hash(password, salt_hex, iterations=ITERATIONS):
    """PBKDF2-HMAC-SHA256(password, salt) → hex digest. Deterministic given the inputs."""
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             bytes.fromhex(salt_hex), iterations)
    return dk.hex()


def load_auth(path=None):
    """Return the stored {user, salt, iter, hash} dict, or None if unconfigured/unreadable.
    `path=None` resolves `AUTH_PATH` at CALL time (not def time) so the live module value
    is honored."""
    path = path or AUTH_PATH
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if not all(k in d for k in ("user", "salt", "hash")):
        return None
    return d


def is_configured(path=None):
    """True if a username/password is set up → the web gate should ask for user/pass
    rather than a raw token."""
    return load_auth(path) is not None


def verify(user, password, *, path=None, auth=None):
    """Constant-time check of (user, password) against the stored hash. False on any
    mismatch or if auth isn't configured. Never raises on bad input."""
    d = auth if auth is not None else load_auth(path)
    if not d:
        return False
    try:
        cand = _hash(password or "", d["salt"], int(d.get("iter", ITERATIONS)))
    except (ValueError, TypeError):
        return False
    user_ok = hmac.compare_digest((user or ""), str(d.get("user", "")))
    pass_ok = hmac.compare_digest(cand, str(d.get("hash", "")))
    return user_ok and pass_ok


def set_auth(user, password, *, path=None, iterations=ITERATIONS):
    """Write the salted PBKDF2 hash for (user, password) to `path` (0600). Returns the
    path. Generates a fresh random salt each call (rotating the password rotates salt)."""
    path = path or AUTH_PATH
    user = (user or "").strip()
    if not user:
        raise ValueError("用户名不能为空")
    if len(password or "") < 6:
        raise ValueError("密码至少 6 位")
    salt = secrets.token_hex(16)
    rec = {"user": user, "salt": salt, "iter": iterations,
           "hash": _hash(password, salt, iterations)}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # write 0600 from creation, not after (avoid a brief world-readable window)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)
    os.chmod(path, 0o600)
    return path


def main(argv=None):
    """Interactive setter — run ON the host: `python3 -m jl.webauth`. Prompts for a
    username + password (password not echoed) and writes the hash. The password never
    leaves this process (no chat / no Agent / no env)."""
    import getpass
    print("设置 AMR 网页登录的用户名 / 密码（密码只存哈希，不存明文）")
    user = input("用户名: ").strip()
    if not user:
        print("× 用户名不能为空"); return 1
    p1 = getpass.getpass("密码（至少 6 位，输入不显示）: ")
    p2 = getpass.getpass("再输一次确认: ")
    if p1 != p2:
        print("× 两次密码不一致"); return 1
    try:
        path = set_auth(user, p1)
    except ValueError as e:
        print("×", e); return 1
    print(f"✅ 已写入 {path}（权限 600）。现在用「{user} + 你的密码」即可登录 AMR，不用再输 token。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
