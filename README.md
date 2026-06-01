# remote-paste-iterm2

[![CI](https://github.com/jaredatch/remote-paste-iterm2/actions/workflows/ci.yml/badge.svg)](https://github.com/jaredatch/remote-paste-iterm2/actions/workflows/ci.yml) ![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg) ![Platform: macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg) ![Requires: iTerm2](https://img.shields.io/badge/requires-iTerm2-black.svg)

Paste a screenshot into Claude Code or Codex running on a remote Mac. Just hit Ctrl+V, the same as you would locally.

Ask any AI assistant how to do this and it'll tell you it's impossible: *"the clipboard can't cross SSH."* It's wrong. Here's the workaround that actually works, tmux and all.

## Quickstart

```sh
git clone https://github.com/jaredatch/remote-paste-iterm2.git
cd remote-paste-iterm2
./install.sh
```

Then two one-time steps in iTerm2: enable the Python API and bind Ctrl+V. Details in [Install](#install).

## The problem

Terminal agents read images from the system clipboard when you paste. Locally that's fine. Over SSH the agent reads the remote clipboard, which is empty, and the image never crosses the wire because a terminal stream carries text, not pixels. So you fall back to scp or typing out a file path, every single time. Claude Code even ships a consolation message for it: *"No image found in clipboard. You're SSH'd; try scp?"*

## How it works

Don't push the image through the terminal. Put it where the agent is already looking, the remote clipboard.

1. You press Ctrl+V. iTerm2 hands the keypress to a small Python script on your Mac.
2. The script grabs the image off your local clipboard and copies it onto the **remote** machine's clipboard over your existing SSH connection.
3. It delivers a genuine Ctrl+V keypress to the remote agent.
4. The agent reads the freshly populated remote clipboard and attaches the image, exactly like a local paste, without touching a file path or scp.

Step 3 is where everyone gets stuck. Under tmux control mode (`-CC`), an injected Ctrl+V shows up as the literal text `0x16` instead of a keystroke, because control mode wraps it in a bracketed paste. The fix: let tmux deliver the key itself with `tmux send-keys`, which counts as a real keypress. remote-paste detects your session type and picks the right delivery on its own.

Your clipboard image only ever travels to the host you're already connected to. Nothing else leaves your machine, and there's no telemetry.

## Requirements

**On your Mac (local):**

- iTerm2 3.3 or newer, with the Python API enabled (Settings → General → Magic → Enable Python API). The scripting API landed in 3.3.
- No Python to install. iTerm2 bundles its own runtime for scripts.
- `pngpaste` is optional but recommended (`brew install pngpaste`). Without it, the clipboard is read with `osascript`.

**On the remote:**

- macOS. The clipboard is loaded with `osascript`, so the box you SSH into has to be a Mac. Linux is on the roadmap.
- A logged-in GUI session under the same user you SSH as, so the clipboard is reachable. A Mac sitting at a logged-in desktop works; a headless box with no console session does not.
- tmux only if you use it. The script handles tmux `-CC`, plain SSH, and local sessions on its own.

If you run terminal agents on a remote Mac over SSH, this kills a daily papercut. If you don't, you don't need it. That's the whole audience, and it's enough.

## Install

```sh
git clone https://github.com/jaredatch/remote-paste-iterm2.git
cd remote-paste-iterm2
./install.sh
```

The installer copies the script into iTerm2's AutoLaunch folder and checks your dependencies. Two one-time manual steps remain:

**1. Enable the Python API**
iTerm2 → Settings → General → Magic → check *Enable Python API*.

**2. Bind Ctrl+V**
iTerm2 → Settings → Keys → Key Bindings → `+`, then:

| Field | Value |
| --- | --- |
| Keyboard Shortcut | `⌃V` |
| Action | Invoke Script Function |
| Function call | `remote_paste(session_id: id)` |

Then start it without restarting iTerm2: **Scripts menu → AutoLaunch → remote_paste.py**. After that it launches with iTerm2.

The binding is global, but it only bridges when you're in a remote session with an image on the clipboard. Everywhere else Ctrl+V behaves normally, so vim's visual-block and shell quoting still work.

## Supported setups

remote-paste figures out your session type and delivers the keypress the right way, with nothing to configure. Detection reads the session's command line and tmux role, so it works from any profile, including the default one.

| Your session | How the keypress is delivered |
| --- | --- |
| SSH + tmux `-CC` (iTerm2 integration) | `tmux send-keys C-v` over SSH |
| SSH, plain or regular tmux | `Ctrl+V` straight down the SSH pty |
| Local | `Ctrl+V` to the local agent |

## Configuration

None required. remote-paste finds your tools and reuses whatever SSH host you connected with.

For overrides (a non-standard tmux path on the remote, a fallback host, a custom temp location), copy `config.example.json` to `~/.config/remote-paste/config.json` and set only the keys you need. Each field is documented inline in the example.

## Troubleshooting

The log is the first place to look:

```sh
tail -f ~/Library/Logs/remote_paste.log
```

A successful paste writes a line like `paste[-CC]: host=my-server tmux=work image=148293 ok=True`.

- **Pasting inserts literal `0x16`.** The script isn't delivering a real keystroke. Confirm the key binding is set to *Invoke Script Function* with exactly `remote_paste(session_id: id)`, and that the script is running (Scripts → Console).
- **"No image found in clipboard."** Your clipboard had no image when you pasted. On macOS, Cmd+Shift+Ctrl+4 copies a screenshot to the clipboard; plain Cmd+Shift+4 saves it to a file instead. If `image=0` shows in the log, the image never reached the script. Installing `pngpaste` helps with unusual clipboard formats.
- **`pngpaste: command not found` in the log.** Harmless. The script falls back to osascript. Install `pngpaste` to silence it.
- **Image lands in the wrong tmux pane.** remote-paste targets the active pane of the focused window. If you split a window and the focus tracking is off, open an issue.

## Limitations and roadmap

Today:

- iTerm2 only. The Python API and tmux `-CC` integration are what make this work, and they're iTerm2 features.
- macOS remote only.
- The remote needs a logged-in GUI session for clipboard access.
- Pane targeting follows the focused window's active pane. The common case is covered; a precise per-pane address isn't there yet.

Planned:

- Linux remote support (`xclip` / `wl-copy`), which terminal agents already read from.
- Precise tmux pane targeting.
- A path-injection fallback for setups without clipboard access.

Contributions welcome, especially Linux testing.

## License

MIT. See [LICENSE](LICENSE).
