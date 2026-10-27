#!/usr/bin/env bash
# Install gstack (https://github.com/garrytan/gstack) in Claude Code cloud
# sessions, which start with an empty home directory. Local machines install
# it once by hand (see CLAUDE.md), so this does nothing outside the cloud.
set -uo pipefail

[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0

GSTACK_DIR="$HOME/.claude/skills/gstack"
[ -d "$GSTACK_DIR/bin" ] && exit 0

mkdir -p "$HOME/.claude/skills"
git clone --single-branch --depth 1 https://github.com/garrytan/gstack.git "$GSTACK_DIR" >&2 \
  && (cd "$GSTACK_DIR" && ./setup --team --no-browser </dev/null >&2) \
  || echo "gstack install failed; gstack skills are unavailable this session" >&2
exit 0
