"""The wire protocol between the MCP process and Blender. Pure Python, no bpy, so it
can be tested on its own.

One JSON object per message, terminated by a NUL byte (the same framing Blender's own
Lab MCP uses). A request names a tool from a fixed table and passes keyword arguments:

    {"id": 1, "token": "…", "tool": "scene", "args": {}}\\0
    {"id": 1, "ok": true, "result": {…}}\\0

There is deliberately no "run this code" tool: only the names in the table can be
called, so a client cannot reach the rest of Blender.
"""

from __future__ import annotations

import base64
import hmac
import inspect
import json
import os

MAX_MESSAGE = 4 << 20          # 4 MiB: generous for code, far below a runaway payload
MAX_IMAGE = 24 << 20
PROTOCOL = 1


class Decoder:
    """Accumulates bytes from a socket and yields whole messages."""

    def __init__(self, limit=MAX_MESSAGE):
        self._buffer = bytearray()
        self.limit = limit
        self.overflowed = False

    def feed(self, chunk):
        if self.overflowed:
            return
        self._buffer.extend(chunk)
        if len(self._buffer) > self.limit:
            self.overflowed = True
            self._buffer.clear()

    def messages(self):
        """Yield each complete message as a dict, or a ('error', text) pair if unreadable."""
        while True:
            end = self._buffer.find(b"\0")
            if end < 0:
                return
            raw = bytes(self._buffer[:end])
            del self._buffer[:end + 1]
            try:
                obj = json.loads(raw.decode("utf-8"))
            except Exception as exc:
                yield {"_malformed": f"{type(exc).__name__}: {exc}"}
                continue
            yield obj if isinstance(obj, dict) else {"_malformed": "a request must be an object"}


def encode(obj):
    return json.dumps(obj, default=_plain).encode("utf-8") + b"\0"


def _plain(value):
    """Anything the tools return that json doesn't know: make it readable, not an error."""
    for attr in ("tolist", "to_list"):
        if hasattr(value, attr):
            try:
                return getattr(value, attr)()
            except Exception:
                pass
    return repr(value)


def authorised(request, token):
    if not token:
        return True
    given = request.get("token")
    return isinstance(given, str) and hmac.compare_digest(given, token)


def describe(dispatch):
    """The tool list a client can discover: name, docstring, and argument names."""
    tools = []
    for name, fn in sorted(dispatch.items()):
        try:
            sig = inspect.signature(fn)
            args = [{"name": p.name,
                     "required": p.default is inspect.Parameter.empty and
                     p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD),
                     "default": None if p.default is inspect.Parameter.empty else _plain_default(p.default),
                     "any_keyword": p.kind == p.VAR_KEYWORD}
                    for p in sig.parameters.values()]
        except (TypeError, ValueError):
            args = []
        doc = (inspect.getdoc(fn) or "").strip()
        tools.append({"name": name, "summary": doc.split("\n\n")[0], "doc": doc, "args": args})
    return tools


def _plain_default(value):
    return value if isinstance(value, (str, int, float, bool, type(None))) else repr(value)


def handle(request, dispatch, token=None):
    """Run one request against the tool table. Never raises; always returns a response."""
    reply = {"id": request.get("id"), "ok": False}
    if "_malformed" in request:
        reply["error"] = f"could not read the request: {request['_malformed']}"
        return reply
    if not authorised(request, token):
        reply["error"] = "unauthorised: wrong or missing token"
        return reply
    name = request.get("tool")
    if name == "tools":
        return {"id": request.get("id"), "ok": True,
                "result": {"protocol": PROTOCOL, "tools": describe(dispatch)}}
    fn = dispatch.get(name)
    if fn is None:
        reply["error"] = (f"no tool named '{name}'. Available: "
                          + ", ".join(sorted(dispatch)) + ", tools")
        return reply
    args = request.get("args") or {}
    if not isinstance(args, dict):
        reply["error"] = "args must be an object"
        return reply
    if any(not isinstance(k, str) for k in args):
        reply["error"] = "argument names must be strings"
        return reply
    try:
        result = fn(**args)
    except TypeError as exc:
        reply["error"] = f"{name}: {exc}"
        return reply
    except Exception as exc:
        reply["error"] = f"{name} failed: {type(exc).__name__}: {exc}"
        return reply
    return {"id": request.get("id"), "ok": True, "result": result}


def read_image(path, max_bytes=MAX_IMAGE):
    """A rendered PNG as base64, for clients that can't reach the filesystem."""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        return {"ok": False, "error": f"no file at {path}"}
    size = os.path.getsize(path)
    if size > max_bytes:
        return {"ok": False, "error": f"{size:,} bytes is too big to send (limit {max_bytes:,})"}
    with open(path, "rb") as fh:
        data = fh.read()
    return {"ok": True, "path": path, "bytes": size, "format": "png",
            "image_base64": base64.b64encode(data).decode("ascii")}
