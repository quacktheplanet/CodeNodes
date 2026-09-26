"""The CodeNodes server that lives inside Blender.

Blender's Python is not thread-safe, so there is no worker thread here. The listening
socket is non-blocking and every step — accept, read, run the tool, reply — happens
inside a ``bpy.app.timers`` callback, which already runs on the main thread. That
removes the thread/queue/lock problem rather than solving it. (The same approach
Blender's own Lab MCP takes.)

It listens on localhost only, needs a shared token, and can only call the fixed set of
functions in `agent` — there is no "run this code" tool.
"""

from __future__ import annotations

import os
import secrets
import socket
import time

import bpy

from . import agent, rpc

HOST = "127.0.0.1"
DEFAULT_PORT = 9877            # not 9876: leave that to blender-mcp
BUSY_INTERVAL = 0.05
IDLE_INTERVAL = 0.5
IDLE_AFTER = 5.0               # seconds of quiet before backing off
CLIENT_TIMEOUT = 30.0          # drop a client that goes quiet mid-message

_state = {"socket": None, "clients": [], "last": 0.0, "port": None, "token": None,
          "served": 0, "errors": 0}


def token_path():
    return os.path.join(bpy.utils.user_resource('CONFIG', create=True), "codenodes_token.json")


def load_token(create=True):
    """The shared secret, kept next to Blender's config. Created on first use."""
    import json
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
             ("help", "make", "set_params", "bake", "scene", "frame", "look_at", "light",
              "render", "viewport", "code", "remove")}
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


def start(port=DEFAULT_PORT, token=None):
    """Open the socket and begin polling. Returns a status dict."""
    if _state["socket"] is not None:
        return status()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((HOST, int(port)))
    except OSError as exc:
        sock.close()
        return {"ok": False, "error": f"could not listen on {HOST}:{port} — {exc}"}
    sock.listen(4)
    sock.setblocking(False)
    _state.update(socket=sock, clients=[], last=time.time(), port=int(port),
                  token=token or load_token(), served=0, errors=0)
    if not bpy.app.timers.is_registered(_poll):
        bpy.app.timers.register(_poll, first_interval=BUSY_INTERVAL, persistent=True)
    print(f"CodeNodes: listening on {HOST}:{port}")
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
    return {"ok": True, "running": False}


def status():
    """Whether the server is up, and where. Also a tool, so a client can check itself."""
    return {"ok": True, "running": _state["socket"] is not None, "host": HOST,
            "port": _state["port"], "clients": len(_state["clients"]),
            "served": _state["served"], "errors": _state["errors"],
            "protocol": rpc.PROTOCOL, "blender": bpy.app.version_string}


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
        table, token = dispatch_table(), _state["token"]
        now = time.time()
        alive = []
        for client in _state["clients"]:
            keep = _serve(client, table, token)
            if keep and not client.out and now - client.seen > CLIENT_TIMEOUT:
                keep = False                        # idle and nothing pending
            if keep:
                alive.append(client)
            else:
                client.close()
        _state["clients"] = alive
    except Exception:
        import traceback
        traceback.print_exc()                       # never let a bug kill the timer
    return BUSY_INTERVAL if time.time() - _state["last"] < IDLE_AFTER else IDLE_INTERVAL


# ---- Blender UI ----------------------------------------------------------------------

class CODENODES_OT_server(bpy.types.Operator):
    bl_idname = "codenodes.server"
    bl_label = "CodeNodes Server"
    bl_description = "Start or stop the local server an assistant connects to"

    action: bpy.props.EnumProperty(items=[('START', "Start", ""), ('STOP', "Stop", "")],
                                   default='START', options={'HIDDEN'})

    def execute(self, context):
        if self.action == 'STOP':
            stop()
            self.report({'INFO'}, "CodeNodes server stopped")
            return {'FINISHED'}
        result = start()
        if not result.get("ok", True) or not result.get("running"):
            self.report({'ERROR'}, result.get("error", "could not start"))
            return {'CANCELLED'}
        self.report({'INFO'}, f"CodeNodes server on {HOST}:{result['port']}")
        return {'FINISHED'}


class CODENODES_PT_server(bpy.types.Panel):
    bl_label = "Assistant"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "CodeNodes"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        info = status()
        if info["running"]:
            layout.label(text=f"Listening on {HOST}:{info['port']}", icon='CHECKMARK')
            layout.label(text=f"{info['clients']} connected · {info['served']} calls")
            layout.operator("codenodes.server", text="Stop", icon='PAUSE').action = 'STOP'
        else:
            layout.label(text="Not running", icon='RADIOBUT_OFF')
            layout.operator("codenodes.server", text="Start", icon='PLAY').action = 'START'
        layout.label(text="Local only, and it needs the token")


classes = (CODENODES_OT_server, CODENODES_PT_server)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    stop()
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
