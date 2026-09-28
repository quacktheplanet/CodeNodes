"""A small MCP (Model Context Protocol) server over stdio, standard library only.

Just enough of the protocol for tools: initialize, tools/list, tools/call, ping. It keeps
the familiar decorator shape, so a tool is an ordinary typed function:

    mcp = MCPServer("CodeNodes")

    @mcp.tool()
    def scene() -> dict:
        '''What is in the scene.'''
        return {...}

Argument types come from the annotations (str, int, float, bool, dict, list and
`X | None`), the description from the docstring. A tool may return a dict/list/str
(sent as text), an `Image`, or a list mixing text and images. Exceptions come back as
an error the model can read.
"""

from __future__ import annotations

import base64
import inspect
import json
import sys
import traceback
import typing

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean",
               dict: "object", list: "array"}


class Image:
    """An image to hand back to the model (it can look at it)."""

    def __init__(self, data: bytes, format: str = "png"):
        self.data = data
        self.format = format

    def content(self):
        return {"type": "image", "data": base64.b64encode(self.data).decode("ascii"),
                "mimeType": f"image/{self.format}"}


def _schema_for(annotation):
    """(json schema, nullable) for one annotation."""
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin in (typing.Union, getattr(__import__("types"), "UnionType", object)):
        rest = [a for a in args if a is not type(None)]
        schema, _ = _schema_for(rest[0]) if len(rest) == 1 else ({}, False)
        return schema, type(None) in args
    base = origin or annotation
    if base in _JSON_TYPES:
        return {"type": _JSON_TYPES[base]}, False
    return {}, False


def _input_schema(fn):
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        hints = {}
    props, required = {}, []
    for p in inspect.signature(fn).parameters.values():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        schema, nullable = _schema_for(hints.get(p.name, inspect.Parameter.empty))
        schema = dict(schema)
        if nullable and "type" in schema:
            schema["type"] = [schema["type"], "null"]
        if p.default is inspect.Parameter.empty:
            required.append(p.name)
        elif isinstance(p.default, (str, int, float, bool)) or p.default is None:
            schema["default"] = p.default
        props[p.name] = schema
    return {"type": "object", "properties": props, "required": required}


def _content(result):
    """A tool's return value as MCP content items."""
    items = result if isinstance(result, list) and any(isinstance(r, Image) for r in result) else [result]
    out = []
    for item in items:
        if isinstance(item, Image):
            out.append(item.content())
        elif isinstance(item, str):
            out.append({"type": "text", "text": item})
        else:
            out.append({"type": "text", "text": json.dumps(item, indent=1, default=str)})
    return out


class MCPServer:
    def __init__(self, name, version="0.1.0", instructions=""):
        self.name = name
        self.version = version
        self.instructions = instructions
        self.tools = {}

    def tool(self):
        def wrap(fn):
            doc = inspect.getdoc(fn) or ""
            self.tools[fn.__name__] = {"fn": fn, "spec": {
                "name": fn.__name__, "description": doc, "inputSchema": _input_schema(fn)}}
            return fn
        return wrap

    # -- protocol ---------------------------------------------------------------------------
    def call(self, name, arguments):
        entry = self.tools.get(name)
        if entry is None:
            return {"content": [{"type": "text", "text": f"unknown tool '{name}'"}], "isError": True}
        try:
            result = entry["fn"](**(arguments or {}))
        except TypeError as exc:
            return {"content": [{"type": "text", "text": f"{name}: {exc}"}], "isError": True}
        except Exception as exc:
            return {"content": [{"type": "text", "text": str(exc) or type(exc).__name__}], "isError": True}
        return {"content": _content(result), "isError": False}

    def handle(self, msg):
        method = msg.get("method")
        mid = msg.get("id")
        if mid is None:
            return None                                        # a notification
        if method == "initialize":
            asked = (msg.get("params") or {}).get("protocolVersion")
            result = {"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                      "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": self.name, "version": self.version}}
            if self.instructions:
                result["instructions"] = self.instructions
            return {"jsonrpc": "2.0", "id": mid, "result": result}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": mid, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": mid,
                    "result": {"tools": [t["spec"] for t in self.tools.values()]}}
        if method == "tools/call":
            params = msg.get("params") or {}
            return {"jsonrpc": "2.0", "id": mid,
                    "result": self.call(params.get("name"), params.get("arguments"))}
        if method in ("resources/list", "prompts/list"):
            return {"jsonrpc": "2.0", "id": mid, "result": {method.split("/")[0]: []}}
        if method == "resources/templates/list":
            return {"jsonrpc": "2.0", "id": mid, "result": {"resourceTemplates": []}}
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": f"unknown method {method}"}}

    def run(self):
        try:
            sys.stdout.reconfigure(encoding="utf-8", newline="\n")
            sys.stdin.reconfigure(encoding="utf-8")
        except Exception:
            pass
        while True:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                self._send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
                continue
            for m in (msg if isinstance(msg, list) else [msg]):
                try:
                    reply = self.handle(m)
                except Exception:
                    traceback.print_exc(file=sys.stderr)
                    reply = {"jsonrpc": "2.0", "id": m.get("id"),
                             "error": {"code": -32603, "message": "internal error"}}
                if reply is not None:
                    self._send(reply)

    @staticmethod
    def _send(msg):
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()
