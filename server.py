#!/usr/bin/env python3
"""Claude Computer-Use MCP server for GNOME/Wayland (Ubuntu).

A zero-SDK, stdio JSON-RPC MCP server that gives Claude Code desktop control
on a Wayland session where the official Computer Use feature isn't available:

  * screenshots  -> xdg-desktop-portal Screenshot (no prompt on GNOME 50)
  * mouse        -> a virtual ABSOLUTE pointer via /dev/uinput (evdev)
  * keyboard     -> a virtual keyboard via /dev/uinput (evdev)

The uinput devices enter below the compositor exactly like real USB hardware,
so they work identically under Wayland and X11. We never ``grab`` anything, so
the user's real mouse/keyboard stay live and can always take over.

Dependencies (all already present on the target machine): python3, evdev,
PyGObject (gi), Pillow. No `mcp` package required.
"""
from __future__ import annotations

import base64
import json
import os
import struct
import sys
import time
import traceback

# ---------------------------------------------------------------------------
# stderr logging only -- stdout is the JSON-RPC channel and must stay clean.
# ---------------------------------------------------------------------------
def log(*a):
    print("[computer-use]", *a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Screenshot via xdg-desktop-portal (works non-interactively on GNOME 50).
# ---------------------------------------------------------------------------
import gi  # noqa: E402
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

_bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
_shot_seq = 0


def _portal_screenshot() -> str:
    """Trigger a portal screenshot; return the local PNG path."""
    global _shot_seq
    _shot_seq += 1
    token = "cu%d_%d" % (os.getpid(), _shot_seq)
    loop = GLib.MainLoop()
    out = {}
    sender = _bus.get_unique_name()
    handle = "/org/freedesktop/portal/desktop/request/%s/%s" % (
        sender[1:].replace(".", "_"), token)

    def on_resp(conn, s, obj, iface, sig, params):
        code, res = params.unpack()
        out["code"] = code
        out["uri"] = res.get("uri", "")
        loop.quit()

    sub = _bus.signal_subscribe(
        "org.freedesktop.portal.Desktop",
        "org.freedesktop.portal.Request", "Response", handle, None,
        Gio.DBusSignalFlags.NONE, on_resp)
    opts = {
        "interactive": GLib.Variant("b", False),
        "handle_token": GLib.Variant("s", token),
    }
    _bus.call_sync(
        "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
        "org.freedesktop.portal.Screenshot", "Screenshot",
        GLib.Variant("(sa{sv})", ("", opts)), None,
        Gio.DBusCallFlags.NONE, 20000, None)
    GLib.timeout_add_seconds(15, lambda: (out.update(code=99), loop.quit()))
    loop.run()
    _bus.signal_unsubscribe(sub)
    if out.get("code") != 0 or not out.get("uri"):
        raise RuntimeError("portal screenshot failed (code %s)" % out.get("code"))
    return out["uri"].replace("file://", "")


def _png_size(path: str):
    with open(path, "rb") as f:
        head = f.read(24)
    return struct.unpack(">II", head[16:24])


# ---------------------------------------------------------------------------
# Virtual input devices via /dev/uinput.
# ---------------------------------------------------------------------------
from evdev import UInput, AbsInfo, ecodes as E  # noqa: E402

# Detect screen size once from a real screenshot so the pointer's coordinate
# space is identical to screenshot pixels (click where you see). If that fails
# (e.g. launched while the screen is locked), fall back to 1920x1080 so the
# server still comes up instead of being dead for the whole session.
try:
    _boot_shot = _portal_screenshot()
    SCREEN_W, SCREEN_H = _png_size(_boot_shot)
    try:
        os.remove(_boot_shot)
    except OSError:
        pass
    log("screen detected: %dx%d" % (SCREEN_W, SCREEN_H))
except Exception as _exc:
    SCREEN_W, SCREEN_H = 1920, 1080
    log("screen detect failed (%s); defaulting to %dx%d" % (_exc, SCREEN_W, SCREEN_H))

# ---- US-layout character map: char -> (KEY_code, needs_shift) --------------
def _mk_char_map():
    m = {}
    for c in "abcdefghijklmnopqrstuvwxyz":
        m[c] = (getattr(E, "KEY_" + c.upper()), False)
        m[c.upper()] = (getattr(E, "KEY_" + c.upper()), True)
    for d in "0123456789":
        m[d] = (getattr(E, "KEY_" + d), False)
    shifted_digits = {"!": "1", "@": "2", "#": "3", "$": "4", "%": "5",
                      "^": "6", "&": "7", "*": "8", "(": "9", ")": "0"}
    for sym, d in shifted_digits.items():
        m[sym] = (getattr(E, "KEY_" + d), True)
    punct = {
        "-": ("MINUS", False), "_": ("MINUS", True),
        "=": ("EQUAL", False), "+": ("EQUAL", True),
        "[": ("LEFTBRACE", False), "{": ("LEFTBRACE", True),
        "]": ("RIGHTBRACE", False), "}": ("RIGHTBRACE", True),
        "\\": ("BACKSLASH", False), "|": ("BACKSLASH", True),
        ";": ("SEMICOLON", False), ":": ("SEMICOLON", True),
        "'": ("APOSTROPHE", False), '"': ("APOSTROPHE", True),
        "`": ("GRAVE", False), "~": ("GRAVE", True),
        ",": ("COMMA", False), "<": ("COMMA", True),
        ".": ("DOT", False), ">": ("DOT", True),
        "/": ("SLASH", False), "?": ("SLASH", True),
        " ": ("SPACE", False), "\t": ("TAB", False), "\n": ("ENTER", False),
    }
    for ch, (name, sh) in punct.items():
        m[ch] = (getattr(E, "KEY_" + name), sh)
    return m


CHAR_MAP = _mk_char_map()

# ---- named keys for key() chords ------------------------------------------
NAMED_KEYS = {
    "enter": E.KEY_ENTER, "return": E.KEY_ENTER, "tab": E.KEY_TAB,
    "space": E.KEY_SPACE, "backspace": E.KEY_BACKSPACE, "bksp": E.KEY_BACKSPACE,
    "delete": E.KEY_DELETE, "del": E.KEY_DELETE, "escape": E.KEY_ESC,
    "esc": E.KEY_ESC, "up": E.KEY_UP, "down": E.KEY_DOWN, "left": E.KEY_LEFT,
    "right": E.KEY_RIGHT, "home": E.KEY_HOME, "end": E.KEY_END,
    "pageup": E.KEY_PAGEUP, "pgup": E.KEY_PAGEUP, "pagedown": E.KEY_PAGEDOWN,
    "pgdn": E.KEY_PAGEDOWN, "insert": E.KEY_INSERT, "menu": E.KEY_COMPOSE,
    "capslock": E.KEY_CAPSLOCK, "printscreen": E.KEY_SYSRQ,
}
for _i in range(1, 13):
    NAMED_KEYS["f%d" % _i] = getattr(E, "KEY_F%d" % _i)
for _d in "abcdefghijklmnopqrstuvwxyz0123456789":
    NAMED_KEYS[_d] = CHAR_MAP[_d][0]

MOD_KEYS = {
    "ctrl": E.KEY_LEFTCTRL, "control": E.KEY_LEFTCTRL,
    "alt": E.KEY_LEFTALT, "option": E.KEY_LEFTALT,
    "shift": E.KEY_LEFTSHIFT,
    "super": E.KEY_LEFTMETA, "meta": E.KEY_LEFTMETA, "win": E.KEY_LEFTMETA,
    "cmd": E.KEY_LEFTMETA, "command": E.KEY_LEFTMETA,
}

# ---- build the two virtual devices ----------------------------------------
_all_key_codes = sorted(set(
    [v[0] for v in CHAR_MAP.values()]
    + list(NAMED_KEYS.values()) + list(MOD_KEYS.values())))
_kbd = UInput({E.EV_KEY: _all_key_codes}, name="Claude Virtual Keyboard")

_ptr = UInput(
    {
        E.EV_KEY: [E.BTN_LEFT, E.BTN_RIGHT, E.BTN_MIDDLE],
        E.EV_ABS: [
            (E.ABS_X, AbsInfo(0, 0, SCREEN_W - 1, 0, 0, 0)),
            (E.ABS_Y, AbsInfo(0, 0, SCREEN_H - 1, 0, 0, 0)),
        ],
        E.EV_REL: [E.REL_WHEEL, E.REL_HWHEEL],
    },
    name="Claude Virtual Pointer",
)
time.sleep(0.9)  # let the compositor enumerate the new devices
_cursor = [SCREEN_W // 2, SCREEN_H // 2]  # last known pointer position

BUTTONS = {"left": E.BTN_LEFT, "right": E.BTN_RIGHT, "middle": E.BTN_MIDDLE}


def _clamp(x, lo, hi):
    return max(lo, min(hi, int(round(x))))


def ptr_move(x, y):
    x = _clamp(x, 0, SCREEN_W - 1)
    y = _clamp(y, 0, SCREEN_H - 1)
    _ptr.write(E.EV_ABS, E.ABS_X, x)
    _ptr.write(E.EV_ABS, E.ABS_Y, y)
    _ptr.syn()
    _cursor[0], _cursor[1] = x, y
    time.sleep(0.03)


def ptr_button(btn_code, down):
    _ptr.write(E.EV_KEY, btn_code, 1 if down else 0)
    _ptr.syn()


def ptr_click(x, y, button="left", count=1):
    if x is not None and y is not None:
        ptr_move(x, y)
    b = BUTTONS[button]
    for i in range(count):
        ptr_button(b, True)
        time.sleep(0.04)
        ptr_button(b, False)
        if i + 1 < count:
            time.sleep(0.08)
    time.sleep(0.05)


def ptr_scroll(x, y, dy, dx=0):
    if x is not None and y is not None:
        ptr_move(x, y)
    # one wheel detent at a time for smoothness
    steps = abs(int(dy))
    sign = 1 if dy > 0 else -1
    for _ in range(steps):
        _ptr.write(E.EV_REL, E.REL_WHEEL, sign)
        _ptr.syn()
        time.sleep(0.02)
    steps = abs(int(dx))
    sign = 1 if dx > 0 else -1
    for _ in range(steps):
        _ptr.write(E.EV_REL, E.REL_HWHEEL, sign)
        _ptr.syn()
        time.sleep(0.02)


def ptr_drag(x1, y1, x2, y2, button="left"):
    b = BUTTONS[button]
    ptr_move(x1, y1)
    time.sleep(0.05)
    ptr_button(b, True)
    time.sleep(0.08)
    # move in a few increments so the compositor registers a drag
    steps = 12
    for i in range(1, steps + 1):
        ptr_move(x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps)
        time.sleep(0.02)
    time.sleep(0.05)
    ptr_button(b, False)
    time.sleep(0.05)


def _tap(code, shift=False):
    if shift:
        _kbd.write(E.EV_KEY, E.KEY_LEFTSHIFT, 1)
        _kbd.syn()
        time.sleep(0.004)
    _kbd.write(E.EV_KEY, code, 1)
    _kbd.syn()
    time.sleep(0.012)
    _kbd.write(E.EV_KEY, code, 0)
    _kbd.syn()
    if shift:
        time.sleep(0.004)
        _kbd.write(E.EV_KEY, E.KEY_LEFTSHIFT, 0)
        _kbd.syn()
    time.sleep(0.012)


def _emit_unicode(ch):
    """IBus Ctrl+Shift+U hex method for characters outside the US map."""
    cp = ord(ch)
    _kbd.write(E.EV_KEY, E.KEY_LEFTCTRL, 1)
    _kbd.write(E.EV_KEY, E.KEY_LEFTSHIFT, 1)
    _kbd.syn()
    _tap(E.KEY_U)
    _kbd.write(E.EV_KEY, E.KEY_LEFTSHIFT, 0)
    _kbd.write(E.EV_KEY, E.KEY_LEFTCTRL, 0)
    _kbd.syn()
    time.sleep(0.02)
    for hc in "%x" % cp:
        code, sh = CHAR_MAP[hc]
        _tap(code, sh)
        time.sleep(0.01)
    _tap(E.KEY_ENTER)


def kbd_type(text):
    unsupported = []
    for ch in text:
        spec = CHAR_MAP.get(ch)
        if spec is not None:
            _tap(spec[0], spec[1])
        else:
            try:
                _emit_unicode(ch)
            except Exception:
                unsupported.append(ch)
    return unsupported


def kbd_chord(combo):
    """combo like 'ctrl+shift+t' or 'Return' or 'alt+F4'."""
    parts = [p.strip() for p in combo.split("+") if p.strip()]
    mods, mains = [], []
    for p in parts:
        pl = p.lower()
        if pl in MOD_KEYS:
            mods.append(MOD_KEYS[pl])
        elif pl in NAMED_KEYS:
            mains.append(NAMED_KEYS[pl])
        elif len(p) == 1 and p in CHAR_MAP:
            mains.append(CHAR_MAP[p][0])
        else:
            raise ValueError("unknown key %r in %r" % (p, combo))
    for mc in mods:
        _kbd.write(E.EV_KEY, mc, 1)
    _kbd.syn()
    time.sleep(0.006)
    for code in mains:
        _kbd.write(E.EV_KEY, code, 1)
        _kbd.syn()
        time.sleep(0.012)
        _kbd.write(E.EV_KEY, code, 0)
        _kbd.syn()
    for mc in reversed(mods):
        _kbd.write(E.EV_KEY, mc, 0)
    _kbd.syn()
    time.sleep(0.02)


# ---------------------------------------------------------------------------
# Screenshot -> base64 PNG, with an optional drawn cursor marker.
# ---------------------------------------------------------------------------
from PIL import Image, ImageDraw  # noqa: E402


def take_screenshot(mark_cursor=True, max_width=0):
    path = _portal_screenshot()
    try:
        img = Image.open(path).convert("RGB")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    real_w, real_h = img.size
    if mark_cursor:
        d = ImageDraw.Draw(img)
        cx, cy = _cursor
        r = 11
        d.line([(cx - r, cy), (cx + r, cy)], fill=(255, 40, 40), width=2)
        d.line([(cx, cy - r), (cx, cy + r)], fill=(255, 40, 40), width=2)
        d.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], outline=(255, 40, 40), width=2)
    scale = 1.0
    if max_width and real_w > max_width:
        scale = max_width / real_w
        img = img.resize((max_width, int(real_h * scale)), Image.LANCZOS)
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    note = "Screen %dx%d. Coordinates for click/move are in full-resolution " \
           "screen pixels (top-left origin)." % (real_w, real_h)
    if scale != 1.0:
        note += " (image shown downscaled; use full-res coords.)"
    return b64, note


# ---------------------------------------------------------------------------
# MCP tool definitions + dispatch.
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "name": "screenshot",
        "description": "Capture the entire desktop and return it as an image. "
                       "A red crosshair marks the current mouse position. Call "
                       "this before clicking so your coordinates match what you "
                       "see.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "max_width": {"type": "integer",
                              "description": "Optional: downscale to this width "
                                             "in px (coords stay full-res). 0 = "
                                             "full resolution."}
            },
        },
    },
    {
        "name": "click",
        "description": "Move the mouse to (x, y) in screen pixels and click.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "button": {"type": "string", "enum": ["left", "right", "middle"],
                           "default": "left"},
                "count": {"type": "integer", "default": 1,
                          "description": "2 for double-click."},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "move",
        "description": "Move the mouse to (x, y) without clicking.",
        "inputSchema": {
            "type": "object",
            "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
            "required": ["x", "y"],
        },
    },
    {
        "name": "drag",
        "description": "Press at (x1, y1), drag to (x2, y2), release.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "x1": {"type": "integer"}, "y1": {"type": "integer"},
                "x2": {"type": "integer"}, "y2": {"type": "integer"},
                "button": {"type": "string", "default": "left"},
            },
            "required": ["x1", "y1", "x2", "y2"],
        },
    },
    {
        "name": "scroll",
        "description": "Scroll the wheel at (x, y). Positive dy scrolls up, "
                       "negative scrolls down; dx scrolls horizontally.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"}, "y": {"type": "integer"},
                "dy": {"type": "integer", "default": -3},
                "dx": {"type": "integer", "default": 0},
            },
        },
    },
    {
        "name": "type",
        "description": "Type a UTF-8 string at the current focus. Newlines press "
                       "Enter. Non-US-layout characters use the IBus hex method "
                       "(best-effort).",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "key",
        "description": "Press a key chord, e.g. 'ctrl+c', 'alt+Tab', 'Return', "
                       "'super', 'ctrl+shift+t', 'alt+F4'. Multiple chords may be "
                       "separated by spaces.",
        "inputSchema": {
            "type": "object",
            "properties": {"keys": {"type": "string"}},
            "required": ["keys"],
        },
    },
]


def call_tool(name, args):
    if name == "screenshot":
        b64, note = take_screenshot(max_width=int(args.get("max_width", 0) or 0))
        return [
            {"type": "image", "data": b64, "mimeType": "image/png"},
            {"type": "text", "text": note},
        ]
    if name == "click":
        ptr_click(args["x"], args["y"], args.get("button", "left"),
                  int(args.get("count", 1)))
        return [{"type": "text", "text": "clicked %s x%d at (%d,%d)" % (
            args.get("button", "left"), int(args.get("count", 1)),
            args["x"], args["y"])}]
    if name == "move":
        ptr_move(args["x"], args["y"])
        return [{"type": "text", "text": "moved to (%d,%d)" % (args["x"], args["y"])}]
    if name == "drag":
        ptr_drag(args["x1"], args["y1"], args["x2"], args["y2"],
                 args.get("button", "left"))
        return [{"type": "text", "text": "dragged (%d,%d)->(%d,%d)" % (
            args["x1"], args["y1"], args["x2"], args["y2"])}]
    if name == "scroll":
        ptr_scroll(args.get("x"), args.get("y"), int(args.get("dy", -3)),
                   int(args.get("dx", 0)))
        return [{"type": "text", "text": "scrolled dy=%s dx=%s" % (
            args.get("dy", -3), args.get("dx", 0))}]
    if name == "type":
        bad = kbd_type(args["text"])
        msg = "typed %d chars" % len(args["text"])
        if bad:
            msg += " (unsupported: %r)" % ("".join(bad))
        return [{"type": "text", "text": msg}]
    if name == "key":
        for chord in args["keys"].split():
            kbd_chord(chord)
        return [{"type": "text", "text": "pressed %s" % args["keys"]}]
    raise ValueError("unknown tool %r" % name)


# ---------------------------------------------------------------------------
# JSON-RPC stdio loop.
# ---------------------------------------------------------------------------
def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    log("ready; %d tools" % len(TOOLS))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        mid = msg.get("id")
        method = msg.get("method")
        try:
            if method == "initialize":
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "protocolVersion": msg.get("params", {}).get(
                        "protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "computer-use", "version": "1.0.0"},
                }})
            elif method in ("notifications/initialized", "initialized"):
                pass
            elif method == "ping":
                send({"jsonrpc": "2.0", "id": mid, "result": {}})
            elif method == "tools/list":
                send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
            elif method == "tools/call":
                params = msg.get("params", {})
                try:
                    content = call_tool(params.get("name"),
                                        params.get("arguments") or {})
                    send({"jsonrpc": "2.0", "id": mid,
                          "result": {"content": content, "isError": False}})
                except Exception as exc:
                    log("tool error:", traceback.format_exc())
                    send({"jsonrpc": "2.0", "id": mid, "result": {
                        "content": [{"type": "text",
                                     "text": "ERROR: %s" % exc}],
                        "isError": True}})
            elif mid is not None:
                send({"jsonrpc": "2.0", "id": mid, "error": {
                    "code": -32601, "message": "method not found: %s" % method}})
        except Exception:
            log("fatal in loop:", traceback.format_exc())
            if mid is not None:
                send({"jsonrpc": "2.0", "id": mid, "error": {
                    "code": -32603, "message": "internal error"}})


if __name__ == "__main__":
    try:
        main()
    finally:
        try:
            _kbd.close()
            _ptr.close()
        except Exception:
            pass
