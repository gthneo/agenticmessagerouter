#!/bin/sh
# sync-contracts.sh — vendor a pinned version of the agentic-contracts truth source.
#
# WHAT / WHY
#   契约真相源住在 gthneo/agentic-contracts 仓。AMR (a CONSUMER) does NOT depend on it at
#   runtime — it VENDORS a committed, read-only copy under vendor/contracts/ pinned to a
#   git TAG. The tag is the language-agnostic artifact (= GitHub Release tarball of the same
#   tree). This is the 先拉式 (pull-based) half of the A/A/先A后B mechanism: the consumer
#   pulls a pinned tag into the repo and commits it, so the contract version is reproducible,
#   diff-reviewable, and offline-available — no network at build/test time.
#
# USAGE
#   scripts/sync-contracts.sh [VERSION]
#     VERSION defaults to the contents of the CONTRACTS_VERSION file (e.g. v0.1.0).
#   Idempotent: re-running for the same version reproduces the same vendored tree.
#
# PUBLIC-SAFE: pulls only the public contracts repo (specs/fixtures, synthetic data only).
#   No secrets, no tokens — nothing here reads or writes credentials.
#
# HUMAN-IN-THE-LOOP: this only WRITES vendor/contracts/ + CONTRACTS_VERSION in the working
#   tree. It does NOT commit/push — a human reviews the diff and commits.
set -eu

REPO_URL="https://github.com/gthneo/agentic-contracts"
# Local fallback checkout (used if the network clone fails in this env — see below).
LOCAL_FALLBACK="${AGENTIC_CONTRACTS_LOCAL:-$HOME/as/agentic-contracts}"

ROOT=$(git rev-parse --show-toplevel)
VERSION_FILE="$ROOT/CONTRACTS_VERSION"
VENDOR_DIR="$ROOT/vendor/contracts"

# Resolve the version: arg > CONTRACTS_VERSION file.
VERSION="${1:-}"
if [ -z "$VERSION" ] && [ -f "$VERSION_FILE" ]; then
    VERSION=$(tr -d ' \t\r\n' < "$VERSION_FILE")
fi
if [ -z "$VERSION" ]; then
    echo "ERROR: no version given and no CONTRACTS_VERSION file" >&2
    exit 1
fi
echo "sync-contracts: vendoring agentic-contracts @ $VERSION"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# 1) Get the pinned tag's tree into $TMP/src (tracked files only; .git dropped after).
#    Primary: shallow clone the public repo at the tag.
#    Fallback: if the network clone fails (offline/CI sandbox), copy from a LOCAL checkout
#    of agentic-contracts at the SAME tag (git archive of the tag → no working-tree drift).
if git clone --depth 1 --branch "$VERSION" "$REPO_URL" "$TMP/src" 2>/dev/null; then
    echo "sync-contracts: cloned $REPO_URL @ $VERSION"
    rm -rf "$TMP/src/.git"   # drop the temp clone's .git — we vendor a plain copy
else
    echo "sync-contracts: network clone failed; falling back to local checkout $LOCAL_FALLBACK" >&2
    if [ ! -d "$LOCAL_FALLBACK/.git" ]; then
        echo "ERROR: no local fallback checkout at $LOCAL_FALLBACK" >&2
        exit 1
    fi
    mkdir -p "$TMP/src"
    # `git archive <tag>` exports exactly the tag's tracked tree — no uncommitted drift.
    ( cd "$LOCAL_FALLBACK" && git archive --format=tar "$VERSION" ) | tar -x -C "$TMP/src"
    echo "sync-contracts: exported $VERSION from $LOCAL_FALLBACK"
fi

# 2) Replace vendor/contracts/ atomically with the new tree, but KEEP our own README.md
#    (the vendoring note) — stash it, wipe, restore.
KEEP_README=""
if [ -f "$VENDOR_DIR/README.md" ]; then
    KEEP_README=$(mktemp)
    cp "$VENDOR_DIR/README.md" "$KEEP_README"
fi
rm -rf "$VENDOR_DIR"
mkdir -p "$VENDOR_DIR"
# copy the exported tree (specs + fixtures + everything tracked at that tag)
cp -R "$TMP/src/." "$VENDOR_DIR/"
# our vendoring note wins over any upstream README at the vendor root
if [ -n "$KEEP_README" ]; then
    cp "$KEEP_README" "$VENDOR_DIR/README.md"
    rm -f "$KEEP_README"
fi

# 3) Record the pinned version.
printf '%s\n' "$VERSION" > "$VERSION_FILE"

echo "sync-contracts: vendored into $VENDOR_DIR (pinned $VERSION)"
echo "sync-contracts: review the diff, then commit (HITL — this script does not commit)."
