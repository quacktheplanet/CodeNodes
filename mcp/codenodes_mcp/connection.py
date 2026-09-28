"""Talking to the CodeNodes server inside Blender. Standard library only.

    from codenodes_mcp.connection import Connection
    with Connection() as blender:
        blender.call("make", kind="mesh", code=..., name="Ball")

Every Blender running CodeNodes registers itself in <home>/instances/<pid>.json (home is
CODENODES_HOME, else ~/.codenodes) with its port and token, so this finds Blender by
itself: the newest one, or the one picked with `use(port)`. CODENODES_PORT and
CODENODES_TOKEN / CODENODES_TOKEN_FILE still override, and older CodeNodes (no registry)
are found on port 9877 with the token from Blender's config folder.
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


NOT_RUNNING = ("no Blender with CodeNodes is open. Open Blender with the CodeNodes add-on "
               "enabled; it lets assistants connect by itself (Preferences › Add-ons › CodeNodes › "
               "'Let assistants connect')")


class BlenderNotRunning(RuntimeError):
    """Nothing is listening: Blender is closed, or CodeNodes' link is off."""


def home():
    return os.environ.get("CODENODES_HOME") or os.path.join(os.path.expanduser("~"), ".codenodes")


def _alive(pid):
    if not pid:
        return False
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))   # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259                                                # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def instances():
    """Running Blenders with CodeNodes, newest first. Files left by a crashed Blender go."""
    found = []
    for path in glob.glob(os.path.join(home(), "instances", "*.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                info = json.load(fh)
        except Exception:
            continue
        if not _alive(info.get("pid")):
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        found.append(info)
    found.sort(key=lambda i: i.get("started") or 0, reverse=True)
    return found


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
        self._fixed_port = int(port or os.environ.get("CODENODES_PORT") or 0) or None
        self._fixed_token = token
        self.port = self._fixed_port or DEFAULT_PORT
        self.token = token
        self.timeout = timeout
        self._sock = None
        self._buffer = bytearray()
        self._id = 0

    # -- plumbing ---------------------------------------------------------------------
    def use(self, port):
        """Talk to the Blender on this port from now on (see `instances`)."""
        self.close()
        self._fixed_port = int(port)
        return self._target()

    def _target(self):
        """Pick the port and token: a fixed port if one was chosen, else the newest Blender."""
        live = instances()
        if self._fixed_port:
            info = next((i for i in live if i.get("port") == self._fixed_port), None)
        else:
            info = live[0] if live else None
        if info is not None:
            self.port = int(info["port"])
            if self._fixed_token is not None:
                self.token = self._fixed_token
            else:
                self.token = os.environ.get("CODENODES_TOKEN") or info.get("token")
        else:
            self.port = self._fixed_port or DEFAULT_PORT
            self.token = self._fixed_token if self._fixed_token is not None else find_token()
        return info

    def connect(self):
        if self._sock is not None:
            return
        self._target()
        try:
            self._sock = socket.create_connection((self.host, self.port), timeout=10)
        except OSError:
            raise BlenderNotRunning(NOT_RUNNING) from None
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
