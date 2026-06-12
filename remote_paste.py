#!/usr/bin/env python3
"""remote-paste — paste clipboard images into terminal AI agents over SSH.

An iTerm2 AutoLaunch script that makes Ctrl+V paste a *clipboard image* into
Claude Code / Codex (or anything that reads the system clipboard on Ctrl+V)
running on a remote Mac over SSH — with or without tmux — exactly as if you
were local.

Why this works
--------------
Tools like Claude Code don't receive image bytes through the terminal; they
read their *own host's* clipboard (on macOS via `osascript ... «class PNGf»`)
when they see a real Ctrl+V keypress. So the job is two steps:
  1. copy the local clipboard image onto the *remote's* clipboard, then
  2. deliver a genuine Ctrl+V to the remote program.

Detection is driven by the iTerm2 session's `commandLine` and `tmuxRole`, so it
works from any profile (not just named ones) and across three cases:

  - tmux -CC (tmuxRole == 'client'): a keystroke sent with async_send_text
    arrives *bracketed* (as a literal paste), so we deliver a real keypress with
    `tmux send-keys -t <session> C-v` over SSH instead.
  - plain ssh / regular tmux: push the clipboard, then async_send_text("\\x16")
    straight down the ssh pty — a real keystroke there.
  - local (no ssh in commandLine): just async_send_text("\\x16").

Setup
-----
1. iTerm2 → Settings → General → Magic → Enable Python API.
2. Copy this file to:
       ~/Library/Application Support/iTerm2/Scripts/AutoLaunch/remote_paste.py
   (or run ./install.sh)
3. Bind the key: iTerm2 → Settings → Keys → Key Bindings → +
       Keyboard Shortcut: ⌃V
       Action:            Invoke Script Function
       Function call:     remote_paste(session_id: id)

Optional config: ~/.config/remote-paste/config.json (see config.example.json).
Requires a macOS remote (osascript clipboard). `pngpaste` is optional — without
it we read the clipboard via osascript.

MIT licensed. https://github.com/jaredatch/remote-paste-iterm2
"""

import asyncio
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
import traceback

import iterm2

__version__ = "1.1"

# Marker matched (via pgrep -f) to find sibling copies of this script. iTerm2
# relaunches AutoLaunch scripts without stopping the prior copy, so duplicates
# pile up and fight over the same RPC registration — see ensure_single_instance.
INSTANCE_MARKER = "AutoLaunch/remote_paste.py"

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

CONFIG_PATH = os.path.expanduser("~/.config/remote-paste/config.json")
LOG_PATH = os.path.expanduser("~/Library/Logs/remote_paste.log")

DEFAULTS = {
    # Local tool paths. null => auto-discover (PATH + common Homebrew dirs).
    "pngpaste": None,
    "ssh": None,
    # Remote tmux binary. null => locate it on the remote at send time.
    "remote_tmux": None,
    # Where the image is staged on the remote before loading onto its clipboard.
    "remote_tmp": "/tmp/iterm-remote-paste.png",
    # SSH host to use when a session is clearly remote (tmux -CC) but the host
    # couldn't be parsed from commandLine. null => give up gracefully.
    "fallback_host": None,
    # Optional regex applied to the iTerm2 profile name to recover a tmux session
    # name when commandLine parsing fails. Group 1 must be the session name.
    "profile_session_regex": None,
}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH) as fh:
            cfg.update(json.load(fh))
    except FileNotFoundError:
        pass
    except Exception:
        log("config error (using defaults):\n" + traceback.format_exc())
    return cfg


def discover(name, configured, candidates):
    """Resolve a local executable: explicit config, then PATH, then candidates."""
    if configured and os.path.exists(configured):
        return configured
    found = shutil.which(name)
    if found:
        return found
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #

def log(msg):
    try:
        with open(LOG_PATH, "a") as fh:
            fh.write(msg + "\n")
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Session-string parsing (profile-independent detection)
# --------------------------------------------------------------------------- #

# Short ssh option flags that consume the following token as their argument.
_SSH_ARG_FLAGS = set("bcDEeFIiJLlmOopQRSWw")


def _tokens(command_line):
    try:
        return shlex.split(command_line)
    except ValueError:
        return command_line.split()


def parse_ssh_host(command_line):
    """Return the ssh destination (user@host or alias) from a command line, else None."""
    toks = _tokens(command_line)
    i = next((k for k, t in enumerate(toks) if os.path.basename(t) == "ssh"), None)
    if i is None:
        return None
    i += 1
    while i < len(toks):
        t = toks[i]
        if t.startswith("-"):
            if len(t) == 2 and t[1] in _SSH_ARG_FLAGS:
                i += 2  # `-p 22` — skip the argument token too
            else:
                i += 1
            continue
        return t  # first non-option token is the destination
    return None


def parse_tmux_session(command_line):
    """Return the tmux `-s <name>` session from a command line, else None.

    The tmux invocation usually lives inside ssh's quoted remote command, so we
    scan the whole string rather than relying on token boundaries.
    """
    if "tmux" not in command_line:
        return None
    m = re.search(r"-s[=\s]+([\w.-]+)", command_line)
    return m.group(1) if m else None


def session_from_profile(profile_name, regex):
    if not regex:
        return None
    try:
        m = re.search(regex, profile_name)
    except re.error:
        log("bad profile_session_regex: %r" % regex)
        return None
    if not m:
        return None
    name = m.group(1)
    return name if re.fullmatch(r"[\w.-]+", name or "") else None


# --------------------------------------------------------------------------- #
# Clipboard / SSH plumbing
# --------------------------------------------------------------------------- #

OSASCRIPT = "/usr/bin/osascript"

# Remote one-liner that loads the piped PNG onto the (macOS) remote clipboard.
def load_clipboard_cmd(remote_tmp):
    return (
        'cat > %s && %s -e \'set the clipboard to '
        '(read (POSIX file "%s") as «class PNGf»)\''
    ) % (shlex.quote(remote_tmp), "osascript", shlex.quote(remote_tmp))


# Shell snippet that locates tmux on the remote (non-interactive ssh has a bare
# PATH, so `command -v tmux` alone often fails).
def remote_tmux_expr(configured):
    if configured:
        return shlex.quote(configured)
    return (
        '"$(command -v tmux 2>/dev/null || '
        'for p in /opt/homebrew/bin/tmux /usr/local/bin/tmux /usr/bin/tmux; '
        'do [ -x "$p" ] && echo "$p" && break; done)"'
    )


def read_clipboard_png(pngpaste):
    """Return PNG bytes if the local Mac clipboard holds an image, else None.

    Prefers pngpaste (handles odd clipboard formats); falls back to osascript so
    pngpaste is not a hard dependency.
    """
    if pngpaste:
        try:
            shot = subprocess.run([pngpaste, "-"], capture_output=True, timeout=5)
            if shot.returncode == 0 and shot.stdout:
                return shot.stdout
        except Exception:
            log("pngpaste failed:\n" + traceback.format_exc())
    # osascript fallback
    try:
        info = subprocess.run([OSASCRIPT, "-e", "clipboard info"],
                              capture_output=True, timeout=5)
        if b"PNGf" not in info.stdout:
            return None
        tmp = tempfile.NamedTemporaryFile(prefix="remote-paste-", suffix=".png",
                                          delete=False)
        tmp.close()
        script = ('set f to (open for access (POSIX file "%s") with write permission)\n'
                  'set eof f to 0\n'
                  'write (the clipboard as «class PNGf») to f\n'
                  'close access f') % tmp.name
        r = subprocess.run([OSASCRIPT, "-e", script], capture_output=True, timeout=10)
        data = b""
        if r.returncode == 0:
            with open(tmp.name, "rb") as fh:
                data = fh.read()
        os.unlink(tmp.name)
        return data or None
    except Exception:
        log("osascript clipboard read failed:\n" + traceback.format_exc())
        return None


def ssh_run(ssh, ssh_host, remote_cmd, stdin=None):
    try:
        res = subprocess.run([ssh, ssh_host, remote_cmd], input=stdin,
                             capture_output=True, timeout=15)
    except Exception:
        log("ssh exception:\n" + traceback.format_exc())
        return False
    if res.returncode != 0:
        log("ssh rc=%d host=%s: %s"
            % (res.returncode, ssh_host, res.stderr.decode("utf-8", "replace")))
        return False
    return True


# --------------------------------------------------------------------------- #
# Main RPC
# --------------------------------------------------------------------------- #

async def main(connection):
    cfg = load_config()
    pngpaste = discover("pngpaste", cfg["pngpaste"],
                        ["/opt/homebrew/bin/pngpaste", "/usr/local/bin/pngpaste"])
    ssh = discover("ssh", cfg["ssh"], ["/usr/bin/ssh"]) or "ssh"
    log("remote_paste %s: starting (pngpaste=%s ssh=%s)" % (__version__, pngpaste, ssh))

    app = await iterm2.async_get_app(connection)

    @iterm2.RPC
    async def remote_paste(session_id):
        session = app.get_session_by_id(session_id)
        if session is None:
            log("no session for id %r" % session_id)
            return
        try:
            command_line = await session.async_get_variable("commandLine") or ""
            profile_name = await session.async_get_variable("profileName") or ""
            tmux_role = await session.async_get_variable("tmuxRole")

            ssh_host = parse_ssh_host(command_line)
            is_cc = tmux_role is not None
            if ssh_host is None and is_cc:
                ssh_host = cfg["fallback_host"]

            # Case 3: local session — native local paste.
            if ssh_host is None:
                await session.async_send_text("\x16")
                return

            png = read_clipboard_png(pngpaste)
            load_cmd = load_clipboard_cmd(cfg["remote_tmp"])

            # Case 1: tmux -CC — deliver a real keystroke via tmux send-keys.
            if is_cc:
                tmux_session = (parse_tmux_session(command_line)
                                or session_from_profile(profile_name,
                                                         cfg["profile_session_regex"]))
                if tmux_session:
                    send = "%s send-keys -t %s C-v" % (
                        remote_tmux_expr(cfg["remote_tmux"]), shlex.quote(tmux_session))
                    cmd = (load_cmd + " && " + send) if png else send
                    ok = ssh_run(ssh, ssh_host, cmd, stdin=png)
                    log("paste[-CC]: host=%s tmux=%s image=%d ok=%s"
                        % (ssh_host, tmux_session, len(png) if png else 0, ok))
                else:
                    # Couldn't resolve the tmux target: load the clipboard and try
                    # a straight keystroke (may arrive bracketed — logged for triage).
                    if png:
                        ssh_run(ssh, ssh_host, load_cmd, stdin=png)
                    await session.async_send_text("\x16")
                    log("paste[-CC?]: unresolved tmux session; cmd=%r profile=%r"
                        % (command_line, profile_name))
                return

            # Case 2: plain ssh / regular tmux — keystroke goes down the pty.
            if png:
                ssh_run(ssh, ssh_host, load_cmd, stdin=png)
            await session.async_send_text("\x16")
            log("paste[ssh]: host=%s image=%d" % (ssh_host, len(png) if png else 0))
        except Exception:
            log("remote_paste error:\n" + traceback.format_exc())

    await remote_paste.async_register(connection)
    log("remote_paste: registered")
    await asyncio.Future()  # stay alive so the RPC remains registered


def sibling_pids(pgrep_output, me, parent):
    """PIDs from `pgrep -f` output to terminate: everything but us and our parent.

    The parent is excluded because it's the it2_api_wrapper.sh shell that launched
    this script — its command line also matches the marker, but killing it would
    take us down with it.
    """
    return [p for p in (int(x) for x in pgrep_output.split() if x.isdigit())
            if p not in (me, parent)]


def ensure_single_instance():
    """Terminate any other running copies of this script before we register.

    iTerm2 starts a *new* instance every time the script is launched (Scripts →
    AutoLaunch) without stopping the old one, and each instance registers the same
    `remote_paste` RPC. Multiple registrations make ⌃V dispatch ambiguous, so
    invocations get routed to a stale instance and silently dropped. Killing
    siblings on startup guarantees exactly one owner of the function.
    """
    me, parent = os.getpid(), os.getppid()
    try:
        out = subprocess.run(["/usr/bin/pgrep", "-f", INSTANCE_MARKER],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        log("singleton: pgrep failed:\n" + traceback.format_exc())
        return
    others = sibling_pids(out, me, parent)
    for pid in others:
        try:
            os.kill(pid, signal.SIGTERM)
            log("singleton: terminated prior instance pid=%d" % pid)
        except ProcessLookupError:
            pass
        except Exception:
            log("singleton: kill pid=%d failed:\n%s" % (pid, traceback.format_exc()))
    # Wait briefly for the killed instances' API connections to close so iTerm2
    # frees the old registration before we claim it.
    for _ in range(20):
        if not any(_alive(p) for p in others):
            break
        time.sleep(0.05)


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


if __name__ == "__main__":
    ensure_single_instance()
    iterm2.run_forever(main)
