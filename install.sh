#!/usr/bin/env bash
# remote-paste installer — copies the script into iTerm2's AutoLaunch folder
# and checks prerequisites. Re-runnable.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$REPO_DIR/remote_paste.py"
AUTOLAUNCH="$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch"
DEST="$AUTOLAUNCH/remote_paste.py"

say()  { printf '\033[1;36m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[1;32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[1;33m!\033[0m %s\n' "$*"; }

say "Installing remote-paste-iterm2"

# 1. iTerm2 present?
if [ ! -d "/Applications/iTerm.app" ] && [ ! -d "$HOME/Applications/iTerm.app" ]; then
  warn "iTerm2 not found in /Applications — remote-paste is iTerm2-only."
fi

# 2. Copy the script into AutoLaunch.
mkdir -p "$AUTOLAUNCH"
cp "$SRC" "$DEST"
ok "Installed → $DEST"

# Stray __pycache__ here makes iTerm2 show a "Cannot Run Script: malformed"
# dialog on every launch (it tries to run everything in AutoLaunch). It appears
# if anything imports the deployed script, e.g. running tests against it.
rm -rf "$AUTOLAUNCH/__pycache__"

# 3. Optional dependency: pngpaste (osascript fallback exists, but pngpaste is nicer).
if command -v pngpaste >/dev/null 2>&1; then
  ok "pngpaste found ($(command -v pngpaste))"
else
  warn "pngpaste not installed (optional). For best results: brew install pngpaste"
fi

cat <<'EOF'

Two manual steps remain (one time):

  1. Enable the Python API:
     iTerm2 → Settings → General → Magic → ✓ Enable Python API

  2. Bind Ctrl+V:
     iTerm2 → Settings → Keys → Key Bindings → +
       Keyboard Shortcut: ⌃V
       Action:            Invoke Script Function
       Function call:     remote_paste(session_id: id)

Then start it now without restarting iTerm2:
  Scripts (menu bar) → AutoLaunch → remote_paste.py
(It auto-starts on every iTerm2 launch from here on.)

Logs: ~/Library/Logs/remote_paste.log
EOF

say "Done."
