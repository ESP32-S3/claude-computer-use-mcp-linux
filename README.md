# Claude Computer Use for Linux (Wayland/GNOME) — `desktop` MCP server

> Give **Claude** (Claude Code / Claude Desktop / any MCP client) real **computer
> use** — screenshots, mouse, and keyboard control of your Linux desktop —
> **where Anthropic's official Computer Use isn't available yet.**

As of the 2026 Claude Desktop Linux beta, Anthropic's built-in **Computer Use**
ships on macOS and Windows only ("coming soon" on Linux, pending Wayland
support). This is a small, dependency-light **Model Context Protocol (MCP)
server** that fills that gap on **Wayland + GNOME** (and X11) using only
facilities already present on a stock Ubuntu install.

No Anthropic API key. No cloud. No extra system packages. It runs locally and
talks MCP over stdio.

---

## Features

- 🖥️ **Screenshot** the whole desktop via `xdg-desktop-portal` — **no prompt**
  on GNOME 50, works under Wayland where `scrot`/`grim` fail.
- 🖱️ **Absolute mouse** control (move, click, double-click, right/middle,
  drag, scroll) via a virtual `/dev/uinput` pointer whose coordinate space is
  **identical to screenshot pixels** — click exactly what you see.
- ⌨️ **Keyboard** control (type UTF-8 text, key chords like `ctrl+shift+t`,
  `alt+Tab`, `super`) via a virtual `/dev/uinput` keyboard.
- 🧭 A **red crosshair** is drawn at the cursor in every screenshot so the model
  always knows where the pointer is.
- 🔓 Never `grab`s input — your real mouse/keyboard stay live and can take over
  instantly. Kernel-level virtual devices work on **Wayland and X11** alike.
- 🪶 Zero-SDK: hand-rolled JSON-RPC stdio server, ~500 lines, no `mcp` pip
  package required.

## Tools exposed

| Tool | Description |
|------|-------------|
| `screenshot` | Capture the desktop (PNG) with a cursor marker. |
| `click` | Move to `(x, y)` and click (`left`/`right`/`middle`, `count` for double). |
| `move` | Move the pointer without clicking. |
| `drag` | Press at `(x1,y1)`, drag to `(x2,y2)`, release. |
| `scroll` | Wheel scroll at `(x, y)` (`dy`/`dx`). |
| `type` | Type a UTF-8 string (US layout + IBus hex fallback for non-ASCII). |
| `key` | Press a key chord, e.g. `ctrl+c`, `alt+F4`, `super`, `Return`. |

In an MCP client these appear as `mcp__desktop__screenshot`, etc.

## How it works

| Concern | Mechanism | Why |
|---------|-----------|-----|
| Screenshot | `org.freedesktop.portal.Desktop` → `Screenshot` (PyGObject/GLib) | GNOME 50 gates `org.gnome.Shell.Screenshot` (`AccessDenied`); the portal returns non-interactively with no dialog. |
| Mouse | virtual **absolute** pointer via `python-evdev` → `/dev/uinput` | Absolute axes avoid pointer-acceleration drift, so screen pixels map 1:1. |
| Keyboard | virtual keyboard via `python-evdev` → `/dev/uinput` | Enters below the compositor like real USB HID — compositor-agnostic. |
| Transport | newline-delimited JSON-RPC 2.0 over stdio | Standard MCP stdio transport; no SDK dependency. |

## Requirements

- Linux with a **Wayland** session (tested: Ubuntu + GNOME 50). X11 works too.
- `python3`, **python-evdev**, **PyGObject** (`gi`), **Pillow** — all standard
  on a desktop Ubuntu; see [`requirements.txt`](requirements.txt).
- Write access to **`/dev/uinput`**. On a normal desktop login this is granted
  to the active session by a `uaccess` ACL (check with `getfacl /dev/uinput`;
  you should see `user:<you>:rw-`). If not, add a udev rule or join the `input`
  group.

## Install

```bash
git clone https://github.com/ESP32-S3/claude-computer-use-mcp-linux.git
cd claude-computer-use-mcp-linux
# deps are usually already present; if needed on Debian/Ubuntu:
#   sudo apt install python3-evdev python3-gi python3-pil
```

### Register with Claude Code (user scope — available in every project)

```bash
claude mcp add desktop --scope user -- python3 "$PWD/server.py"
```

Restart Claude Code (MCP servers load at session start) and the `mcp__desktop__*`
tools appear. Remove with `claude mcp remove desktop --scope user`.

### Register with Claude Desktop / any MCP client

Add to the client's MCP config (e.g. `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "desktop": {
      "command": "python3",
      "args": ["/absolute/path/to/server.py"]
    }
  }
}
```

## Limitations

- Tuned for a **single 1920×1080 @ 1× display**. Multi-monitor and fractional
  HiDPI scaling are not handled — the absolute pointer maps to one output's
  pixel space.
- Screen size is detected once at startup from a screenshot; restart after a
  resolution change.
- The portal writes each screenshot to `~/Pictures/`; the server reads and
  deletes it immediately.

## Security notes

- Runs entirely locally; takes no network action.
- It can see your whole screen and drive mouse/keyboard — treat it like any
  remote-control tool and only enable it for agents you trust.
- Input devices are never grabbed, so you retain physical control at all times.

## License

MIT — see [LICENSE](LICENSE).

---

<sub>**Keywords:** Claude computer use Linux · Claude Desktop Linux computer use ·
Claude Code MCP server · Model Context Protocol desktop automation · Wayland
GUI automation · GNOME screenshot MCP · xdg-desktop-portal screenshot · uinput
mouse keyboard Python · evdev virtual input Wayland · Anthropic Claude desktop
control · AI agent desktop control Ubuntu · computer-use alternative Linux.</sub>
