"""Tests for remote-paste's detection logic.

These cover the pure functions that decide *which host*, *which tmux session*,
and *how to deliver the keypress* from an iTerm2 session's command line. That
logic is where a bad edit could silently route a paste to the wrong machine, so
it's worth pinning down. The actual paste (clipboard, SSH, tmux, iTerm2) needs a
real environment and can't run in CI.

The `iterm2` package isn't installed in CI, so we stub it before importing the
script. The script guards `run_forever` behind `if __name__ == "__main__"`, so
importing it here does not start the event loop.
"""

import os
import sys
import types

sys.modules.setdefault("iterm2", types.ModuleType("iterm2"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import remote_paste as rp  # noqa: E402


# --- parse_ssh_host ------------------------------------------------------- #

def test_host_from_cc_command():
    cmd = 'ssh -A kramer-ts -t "/opt/homebrew/bin/tmux -CC new-session -A -s work"'
    assert rp.parse_ssh_host(cmd) == "kramer-ts"

def test_host_plain_ssh():
    assert rp.parse_ssh_host("ssh my-server") == "my-server"

def test_host_skips_flag_with_argument():
    assert rp.parse_ssh_host("ssh -p 2222 -i ~/.ssh/key me@host") == "me@host"

def test_host_with_absolute_ssh_path():
    assert rp.parse_ssh_host("/usr/bin/ssh -A box") == "box"

def test_host_user_at_host_preserved():
    assert rp.parse_ssh_host("ssh deploy@10.0.0.5") == "deploy@10.0.0.5"

def test_host_never_returns_an_option_token():
    # A malicious-looking command must not yield a host starting with '-'.
    host = rp.parse_ssh_host("ssh -oProxyCommand=evil")
    assert host is None or not host.startswith("-")

def test_no_ssh_is_local():
    assert rp.parse_ssh_host("-zsh") is None
    assert rp.parse_ssh_host("node /usr/local/bin/claude") is None


# --- parse_tmux_session --------------------------------------------------- #

def test_tmux_session_from_nested_cmd():
    cmd = 'ssh h -t "tmux -CC new-session -A -s mission-control -c ~/Projects/mc"'
    assert rp.parse_tmux_session(cmd) == "mission-control"

def test_tmux_session_hyphenated_name():
    assert rp.parse_tmux_session('ssh h -t "tmux -CC new -s agent-recap"') == "agent-recap"

def test_tmux_session_absent():
    assert rp.parse_tmux_session("ssh my-server") is None
    assert rp.parse_tmux_session("-zsh") is None


# --- session_from_profile ------------------------------------------------- #

def test_session_from_profile_regex():
    assert rp.session_from_profile("Project Grock", r"^Project (.+)$") == "Grock"

def test_session_from_profile_no_regex():
    assert rp.session_from_profile("Project Grock", None) is None

def test_session_from_profile_rejects_unsafe_capture():
    # A capture with shell metacharacters must be rejected (not [\w.-]).
    assert rp.session_from_profile("x; rm -rf /", r"^(.+)$") is None


# --- command builders ----------------------------------------------------- #

def test_load_clipboard_cmd_quotes_path_and_loads_png():
    cmd = rp.load_clipboard_cmd("/tmp/x.png")
    assert "PNGf" in cmd
    assert "/tmp/x.png" in cmd

def test_remote_tmux_expr_uses_configured_path():
    assert rp.remote_tmux_expr("/usr/bin/tmux") == "/usr/bin/tmux"

def test_remote_tmux_expr_probes_when_unset():
    expr = rp.remote_tmux_expr(None)
    assert "command -v tmux" in expr


# --- sibling_pids (singleton guard) --------------------------------------- #

def test_sibling_pids_excludes_self_and_parent():
    # pgrep lists us (10), our wrapper parent (9), and one stale instance (42).
    assert rp.sibling_pids("9\n10\n42\n", me=10, parent=9) == [42]

def test_sibling_pids_none_to_kill_on_clean_start():
    # First launch: only this process and its wrapper are present.
    assert rp.sibling_pids("9\n10\n", me=10, parent=9) == []

def test_sibling_pids_kills_multiple_duplicates():
    assert rp.sibling_pids("9 10 14899 14913 25459", me=10, parent=9) == [14899, 14913, 25459]

def test_sibling_pids_ignores_non_numeric_noise():
    assert rp.sibling_pids("10\n\n  \nfoo\n42\n", me=10, parent=9) == [42]
