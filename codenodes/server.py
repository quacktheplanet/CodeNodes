"""The CodeNodes link that lives inside Blender, so an assistant (Claude, over MCP) can
drive CodeNodes.

It starts by itself when the add-on is enabled (Preferences › Add-ons › CodeNodes ›
"Let assistants connect" turns it off), listens on this computer only (127.0.0.1), and
needs a random token that it writes where the MCP server finds it. Nobody has to press
Start or copy anything.

Blender's Python is not thread-safe, so there is no worker thread here. The listening
socket is non-blocking and every step (accept, read, run the tool, reply) happens
inside a ``bpy.app.timers`` callback, which already runs on the main thread. Idle, a tick
every half second costs one non-blocking accept.

Each running Blender registers itself in ``<home>/instances/<pid>.json`` (home is
CODENODES_HOME, else ~/.codenodes) with its port and token, so several Blenders can run
at once: the first takes port 9877, the next 9878, and so on.

It can only call the fixed set of functions in `agent`; there is no "run this code" tool.
"""

from __future__ import annotations

import atexit
import json
import os
import secrets
import socket
import sys
import time

import bpy

from . import agent, rpc

HOST = "127.0.0.1"
DEFAULT_PORT = 9877            # not 9876: leave that to blender-mcp
PORT_TRIES = 10                # 9877..9886, so several Blenders can be open at once
BUSY_INTERVAL = 0.05
IDLE_INTERVAL = 0.5
IDLE_AFTER = 5.0               # seconds of quiet before backing off
CLIENT_TIMEOUT = 30.0          # drop a client that goes quiet mid-message

_state = {"socket": None, "clients": [], "last": 0.0, "port": None, "token": None,
          "served": 0, "errors": 0, "started": 0.0, "error": ""}


# ---- where things are written ------------------------------------------------------------

def home():
    """Where running Blenders register themselves. Shared with the MCP server."""
    base = os.environ.get("CODENODES_HOME") or os.path.join(os.path.expanduser("~"), ".codenodes")
    os.makedirs(os.path.join(base, "instances"), exist_ok=True)
    return base


def instance_path():
    return os.path.join(home(), "instances", f"{os.getpid()}.json")


def _write_instance():
    """Tell the MCP server where this Blender listens. Best effort: never raises."""
    if _state["socket"] is None:
        return
    try:
        info = {"pid": os.getpid(), "port": _state["port"], "token": _state["token"],
                "blender": bpy.app.version_string, "binary": bpy.app.binary_path,
                "file": bpy.data.filepath or "", "started": _state["started"],
                "protocol": rpc.PROTOCOL}
        path = instance_path()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(info, fh)
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    except Exception as exc:
        print(f"CodeNodes: could not register this Blender for assistants ({exc})")


def _remove_instance():
    try:
        os.remove(instance_path())
    except OSError:
        pass


def token_path():
    return os.path.join(bpy.utils.user_resource('CONFIG', create=True), "codenodes_token.json")


def load_token(create=True):
    """The shared secret, kept next to Blender's config. Created on first use."""
    path = token_path()
    try:
        with open(path, encoding="utf-8") as fh:
            token = json.load(fh).get("token")
        if token:
            return token
    except Exception:
        pass
    if not create:
        return None
    token = secrets.token_urlsafe(32)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"token": token}, fh)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    return token


def dispatch_table():
    """Exactly what a client may call. Nothing here can run arbitrary code."""
    table = {name: getattr(agent, name) for name in
             ("help", "make", "set_params", "bake", "bake_to_nodes", "scene", "frame", "look_at", "light",
              "render", "viewport", "code", "remove",
              "nodes_help", "nodes_find", "nodes_describe", "nodes_list", "nodes_read",
              "nodes_write", "nodes_apply", "nodes_check", "nodes_explain", "nodes_edit",
              "nodes_library", "nodes_use", "nodes_set_inputs", "curve", "material",
              "collect", "web_page", "web_shape", "code_node", "code_stage",
              "plan_site", "edit_plan", "build_plan", "place_equipment", "verify", "assets")}
    table["read_image"] = rpc.read_image
    table["status"] = status
    return table


class _Client:
    __slots__ = ("sock", "decoder", "out", "seen")

    def __init__(self, sock):
        self.sock = sock
        self.decoder = rpc.Decoder()
        self.out = bytearray()
        self.seen = time.time()

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def _bind(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform.startswith("win"):
        # SO_REUSEADDR on Windows would let a second Blender bind the same port; this refuses it
        sock.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", -5), 1)
    else:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((HOST, int(port)))
    except OSError:
        sock.close()
        raise
    return sock


def start(port=None, token=None):
    """Open the socket and begin polling. `port` None tries 9877 upward until one is free.
    Returns a status dict."""
    if _state["socket"] is not None:
        return status()
    ports = [int(port)] if port else list(range(DEFAULT_PORT, DEFAULT_PORT + PORT_TRIES))
    sock, last = None, None
    for p in ports:
        try:
            sock = _bind(p)
            port = p
            break
        except OSError as exc:
            last = exc
    if sock is None:
        _state["error"] = f"could not listen on {HOST}:{ports[0]}-{ports[-1]} ({last})"
        return {"ok": False, "running": False, "error": _state["error"]}
    sock.listen(4)
    sock.setblocking(False)
    _state.update(socket=sock, clients=[], last=time.time(), port=int(port),
                  token=token or load_token(), served=0, errors=0, started=time.time(), error="")
    if not bpy.app.timers.is_registered(_poll):
        bpy.app.timers.register(_poll, first_interval=BUSY_INTERVAL, persistent=True)
    _write_instance()
    print(f"CodeNodes: assistants can connect on {HOST}:{port}")
    return status()


def stop():
    for client in _state["clients"]:
        client.close()
    _state["clients"] = []
    if _state["socket"] is not None:
        try:
            _state["socket"].close()
        except Exception:
            pass
    _state["socket"] = None
    if bpy.app.timers.is_registered(_poll):
        bpy.app.timers.unregister(_poll)
    _remove_instance()
    return {"ok": True, "running": False}


def status():
    """Whether the link is up, and where. Also a tool, so a client can check itself."""
    out = {"ok": True, "running": _state["socket"] is not None, "host": HOST,
           "port": _state["port"], "clients": len(_state["clients"]),
           "served": _state["served"], "errors": _state["errors"],
           "protocol": rpc.PROTOCOL, "blender": bpy.app.version_string,
           "file": bpy.data.filepath or ""}
    if _state["error"]:
        out["error"] = _state["error"]
    return out


def _accept():
    while True:
        try:
            conn, _addr = _state["socket"].accept()
        except (BlockingIOError, OSError):
            return
        conn.setblocking(False)
        _state["clients"].append(_Client(conn))


def _serve(client, table, token):
    """Read what has arrived, answer whole requests, push replies out. No blocking."""
    busy = False
    try:
        while True:
            try:
                chunk = client.sock.recv(65536)
            except (BlockingIOError, InterruptedError):
                break
            if not chunk:
                return False                       # the client hung up
            client.decoder.feed(chunk)
            client.seen = time.time()
            busy = True
    except (ConnectionResetError, OSError):
        return False

    if client.decoder.overflowed:
        client.out += rpc.encode({"ok": False, "error": "message too large"})
        client.decoder.overflowed = False

    for request in client.decoder.messages():
        reply = rpc.handle(request, table, token)
        _state["served"] += 1
        if not reply.get("ok"):
            _state["errors"] += 1
        client.out += rpc.encode(reply)
        busy = True

    while client.out:
        try:
            sent = client.sock.send(bytes(client.out[:65536]))
        except (BlockingIOError, InterruptedError):
            break
        except (ConnectionResetError, OSError):
            return False
        if not sent:
            break
        del client.out[:sent]
        busy = True

    if busy:
        _state["last"] = time.time()
    return True


def _poll():
    """The whole server, one tick, on the main thread."""
    if _state["socket"] is None:
        return None                                 # unregister
    try:
        _accept()
        if _state["clients"]:
            table, token = dispatch_table(), _state["token"]
            now = time.time()
            alive = []
            for client in _state["clients"]:
                keep = _serve(client, table, token)
                if keep and not client.out and now - client.seen > CLIENT_TIMEOUT:
                    keep = False                    # idle and nothing pending
                if keep:
                    alive.append(client)
                else:
                    client.close()
            _state["clients"] = alive
    except Exception:
        import traceback
        traceback.print_exc()                       # never let a bug kill the timer
    return BUSY_INTERVAL if time.time() - _state["last"] < IDLE_AFTER else IDLE_INTERVAL


# ---- starting by itself -----------------------------------------------------------------

def _pref_enabled():
    """The add-on preference. None when CodeNodes isn't enabled through Preferences (imported
    by a test or script), in which case nothing starts unless CODENODES_LINK=1."""
    addon = bpy.context.preferences.addons.get(__package__)
    if addon is None or addon.preferences is None:
        return None
    return bool(getattr(addon.preferences, "link_enabled", True))


def wanted():
    """Should the link be running? CODENODES_LINK=1/0 overrides everything (tests)."""
    forced = os.environ.get("CODENODES_LINK")
    if forced is not None:
        return forced.strip().lower() not in ("", "0", "false", "no", "off")
    if bpy.app.background:
        return False                               # command-line renders and scripts: no link
    return bool(_pref_enabled())


def _autostart():
    try:
        if wanted() and _state["socket"] is None:
            result = start()
            if not result.get("running"):
                print(f"CodeNodes: {result.get('error')}")
    except Exception as exc:                       # never let this break Blender's startup
        print(f"CodeNodes: assistant link not started ({exc})")
    return None


def autostart():
    """Start the link shortly after the add-on is enabled (a timer, so enabling stays instant)."""
    if not bpy.app.timers.is_registered(_autostart):
        bpy.app.timers.register(_autostart, first_interval=0.5, persistent=True)


@bpy.app.handlers.persistent
def _on_load(*_args):
    _write_instance()                              # the open file's name, for the MCP's list


def register():
    atexit.register(_remove_instance)
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)
    autostart()


def unregister():
    if bpy.app.timers.is_registered(_autostart):
        bpy.app.timers.unregister(_autostart)
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    stop()
