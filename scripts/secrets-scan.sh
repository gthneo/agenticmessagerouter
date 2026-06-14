#!/bin/sh
# Block real secrets / PII from entering the repo. Scans staged files (pre-commit)
# or all tracked files (CI). The repo is public — fixtures must be synthetic.
# Conventions: synthetic names 张三/李四/王五, wxid_test_*, +8613000000000-range.
set -e

if [ "$1" = "--all" ]; then
    files=$(git ls-files)
else
    files=$(git diff --cached --name-only --diff-filter=ACM)
fi
[ -z "$files" ] && exit 0

# patterns that must never appear in committed code
patterns='Bearer [A-Za-z0-9_-]{20,}|wxid_(axnj|mrws|m7spwo|nc1g|7479)|1865912|1533379|1810607|7479524795812'

# exclude this scanner itself — it necessarily contains the pattern fragments
hits=$(git grep -nE "$patterns" -- $files ':!scripts/secrets-scan.sh' 2>/dev/null || true)
if [ -n "$hits" ]; then
    echo "✋ secrets-scan: real secret/PII detected — commit blocked:" >&2
    echo "$hits" >&2
    echo "Use synthetic fixtures (张三/李四, wxid_test_*) and env/secret files. See .env.example." >&2
    exit 1
fi
exit 0
