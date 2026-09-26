"""Talking to the CodeNodes server inside Blender. Standard library only.

    from codenodes_mcp.connection import Connection
    with Connection() as blender:
        blender.call("make", kind="mesh", code=..., name="Ball")

The server is localhost-only and needs the token Blender wrote next to its config, so
this looks in the obvious places (or `CODENODES_TOKEN` / `CODENODES_TOKEN_FILE`).
"""

from __future__ import annotations

import glob
import json
import os
import socket
import sys
import time

HOST = "127.0.0.1"
DEFAULT_PORT = 9877
TOKEN_FILE = "codenodes_token.json"


class BlenderNotRunning(RuntimeError):
    """Nothing is listening: Blender is closed, or its server has not been started."""


def blender_config_dirs():
    """Where Blender keeps its config, newest version first."""
    if sys.platform.startswith("win"):
        base = os.path.join(os.environ.get("APPDATA", ""), "Blender Foundation", "Blender")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support/Blender")
    else:
        base = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "blender")
    return sorted(glob.glob(os.path.join(base, "*", "config")), reverse=True)


def find_token():
    """The shared secret, or None. Environment first, then Blender's config folders."""
    token = os.environ.get("CODENODES_TOKEN")
    if token:
        return token.strip()
    paths = []
    explicit = os.environ.get("CODENODES_TOKEN_FILE")
    if explicit:
        paths.append(explicit)
    paths += [os.path.join(d, TOKEN_FILE) for d in blender_config_dirs()]
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                token = json.load(fh).get("token")
            if token:
                return token
        except Exception:
            continue
    return None


class Connection:
    """One socket to Blender. Reconnects if the link drops between calls."""

    def __init__(self, host=HOST, port=None, token=None, timeout=300.0):
        self.host = host
        self.port = int(port or os.environ.get("CODENODES_PORT") or DEFAULT_PORT)
        self.token = token if token is not None else find_token()
        self.timeout = timeout
        self._sock = None
        self._buffer = bytearray()
        self._id = 0

    # -- plumbing ---------------------------------------------------------------------
    def connect(self):
        if self._sock is not None:
            return
        try:
            self._sock = socket.create_connection((self.host, self.port), timeout=10)
        except OSError as exc:
            raise BlenderNotRunning(
                f"nothing is listening on {self.host}:{self.port} — open Blender, then "
                f"View3D › Sidebar (N) › CodeNodes › Assistant › Start ({exc})") from None
        self._buffer.clear()

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    def _read(self):
        deadline = time.time() + self.timeout
        while True:
            end = self._buffer.find(b"\0")
            if end >= 0:
                raw = bytes(self._buffer[:end])
                del self._buffer[:end + 1]
                return json.loads(raw.decode("utf-8"))
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError(f"Blender did not reply within {self.timeout:.0f}s")
            self._sock.settimeout(min(remaining, 5.0))
            try:
                chunk = self._sock.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                self.close()
                raise BlenderNotRunning("Blender closed the connection (did it quit?)")
            self._buffer.extend(chunk)

    # -- the one method that matters ------------------------------------------------------
    def call(self, tool, **args):
        """Run a tool in Blender and return its result. Raises RuntimeError on refusal."""
        self.connect()
        self._id += 1
        request = {"id": self._id, "tool": tool, "args": args}
        if self.token:
            request["token"] = self.token
        payload = json.dumps(request).encode("utf-8") + b"\0"
        try:
            self._sock.sendall(payload)
        except OSError:
            self.close()                       # stale socket: one clean retry
            self.connect()
            self._sock.sendall(payload)
        reply = self._read()
        if not reply.get("ok"):
            error = reply.get("error", "unknown error")
            if "unauthorised" in error:
                error += ("\nThe token did not match. It lives in Blender's config folder as "
                          f"{TOKEN_FILE}; set CODENODES_TOKEN if this process cannot read it.")
            raise RuntimeError(error)
        return reply.get("result")

    def tools(self):
        return self.call("tools")["tools"]
