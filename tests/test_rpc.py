"""The wire protocol on its own — no Blender needed.

    python tests/test_rpc.py
"""
import base64
import importlib.util
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = importlib.util.module_from_spec(importlib.util.spec_from_loader("codenodes", loader=None, is_package=True))
pkg.__path__ = [os.path.join(ROOT, "codenodes")]
sys.modules["codenodes"] = pkg
from codenodes import rpc  # noqa: E402

_checks = 0


def check(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        print(f"FAIL: {msg}")
        raise SystemExit(1)
    print(f"  ok: {msg}")


def greet(name, excited=False):
    """Say hello.

    A second paragraph that should not appear in the summary.
    """
    return {"hello": name, "excited": excited}


def explode():
    raise ValueError("boom")


def anything(**kwargs):
    return kwargs


DISPATCH = {"greet": greet, "explode": explode, "anything": anything}


def main():
    # --- framing ---------------------------------------------------------------------
    d = rpc.Decoder()
    d.feed(rpc.encode({"a": 1}) + rpc.encode({"b": 2}))
    check([m["a"] for m in d.messages() if "a" in m] == [1], "two messages in one chunk are split")
    d = rpc.Decoder()
    blob = rpc.encode({"tool": "greet"})
    for byte in blob:                       # arriving one byte at a time
        d.feed(bytes([byte]))
    check([m.get("tool") for m in d.messages()] == ["greet"], "a message split across chunks is rejoined")
    d = rpc.Decoder()
    d.feed(b'{"broken": ' + b"\0")
    check("_malformed" in next(iter(d.messages())), "broken JSON is reported, not raised")
    d = rpc.Decoder()
    d.feed(b'"just a string"\0')
    check("_malformed" in next(iter(d.messages())), "a non-object request is rejected")
    d = rpc.Decoder(limit=64)
    d.feed(b"x" * 100)
    check(d.overflowed and list(d.messages()) == [], "an oversized message is dropped, not buffered")

    # --- dispatch ---------------------------------------------------------------------
    r = rpc.handle({"id": 7, "tool": "greet", "args": {"name": "Ada"}}, DISPATCH)
    check(r["ok"] and r["id"] == 7 and r["result"]["hello"] == "Ada", f"a tool runs and the id comes back ({r})")
    r = rpc.handle({"tool": "greet", "args": {}}, DISPATCH)
    check(not r["ok"] and "missing" in r["error"].lower() or "required" in r["error"].lower(),
          f"a missing argument is explained ({r['error']})")
    r = rpc.handle({"tool": "greet", "args": {"name": "x", "nope": 1}}, DISPATCH)
    check(not r["ok"] and "nope" in r["error"], f"an unexpected argument is explained ({r['error']})")
    r = rpc.handle({"tool": "explode", "args": {}}, DISPATCH)
    check(not r["ok"] and "boom" in r["error"] and "ValueError" in r["error"],
          f"a tool that raises becomes an error reply ({r['error']})")
    r = rpc.handle({"tool": "nothing"}, DISPATCH)
    check(not r["ok"] and "no tool named" in r["error"] and "greet" in r["error"],
          "an unknown tool lists what is available")
    r = rpc.handle({"tool": "greet", "args": [1, 2]}, DISPATCH)
    check(not r["ok"] and "object" in r["error"], "args must be an object")
    r = rpc.handle({"_malformed": "bad json"}, DISPATCH)
    check(not r["ok"] and "could not read" in r["error"], "a malformed request gets a reply too")

    # --- no way to run arbitrary code ---------------------------------------------------
    for attempt in ("exec", "eval", "execute", "execute_blender_code", "__import__", "open"):
        r = rpc.handle({"tool": attempt, "args": {"code": "import os"}}, DISPATCH)
        check(not r["ok"], f"'{attempt}' is not a tool")

    # --- the token -----------------------------------------------------------------------
    r = rpc.handle({"tool": "greet", "args": {"name": "x"}}, DISPATCH, token="secret")
    check(not r["ok"] and "unauthorised" in r["error"], "a request without the token is refused")
    r = rpc.handle({"tool": "greet", "args": {"name": "x"}, "token": "wrong"}, DISPATCH, token="secret")
    check(not r["ok"] and "unauthorised" in r["error"], "a wrong token is refused")
    r = rpc.handle({"tool": "greet", "args": {"name": "x"}, "token": "secret"}, DISPATCH, token="secret")
    check(r["ok"], "the right token is accepted")

    # --- discovery ------------------------------------------------------------------------
    r = rpc.handle({"tool": "tools"}, DISPATCH)
    names = {t["name"] for t in r["result"]["tools"]}
    check(r["ok"] and names == {"greet", "explode", "anything"}, f"'tools' lists the table ({names})")
    greet_spec = next(t for t in r["result"]["tools"] if t["name"] == "greet")
    check(greet_spec["summary"] == "Say hello.", f"the summary is the first line ({greet_spec['summary']!r})")
    args = {a["name"]: a for a in greet_spec["args"]}
    check(args["name"]["required"] and not args["excited"]["required"]
          and args["excited"]["default"] is False, f"argument defaults are described ({args})")
    spec = next(t for t in r["result"]["tools"] if t["name"] == "anything")
    check(spec["args"][0]["any_keyword"], "a **kwargs tool says so")

    # --- encoding oddities -------------------------------------------------------------------
    class Weird:
        def __repr__(self):
            return "<weird>"

    payload = rpc.encode({"v": Weird()})
    check(b"<weird>" in payload, "something unserialisable is described rather than crashing")

    # --- images ---------------------------------------------------------------------------------
    tmp = tempfile.mkdtemp()
    png = os.path.join(tmp, "x.png")
    with open(png, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
    got = rpc.read_image(png)
    check(got["ok"] and base64.b64decode(got["image_base64"])[:4] == b"\x89PNG", "an image comes back as base64")
    check(not rpc.read_image(os.path.join(tmp, "nope.png"))["ok"], "a missing image is explained")
    check(not rpc.read_image(png, max_bytes=10)["ok"], "an oversized image is refused")

    # the MCP process itself: every tool it declares is one the add-on answers, and the files
    # at least parse (a broken edit there once went unnoticed, since nothing here runs it)
    import ast
    mcp_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp", "codenodes_mcp")
    tree = ast.parse(open(os.path.join(mcp_dir, "server.py"), encoding="utf-8").read())
    tools = [f.name for f in tree.body if isinstance(f, ast.FunctionDef)
             and any(getattr(d, "func", None) is not None and getattr(d.func, "attr", "") == "tool"
                     for d in f.decorator_list)]
    called = {n.args[0].value for n in ast.walk(tree) if isinstance(n, ast.Call)
              and getattr(n.func, "id", "") == "_call" and n.args and isinstance(n.args[0], ast.Constant)}
    server_src = open(os.path.join(os.path.dirname(mcp_dir), "..", "codenodes", "server.py"), encoding="utf-8").read()
    answered = set(re.findall(r'"(\w+)"', server_src.split("def dispatch_table")[1].split("return table")[0]))
    check(len(tools) >= 20 and "remove" in tools, f"the MCP server parses and declares {len(tools)} tools")
    check(called <= answered | {"read_image", "status"},
          f"every call it makes is one the add-on answers (missing: {sorted(called - answered - {'read_image', 'status'}) or 'none'})")

    print(f"\nAll {_checks} checks passed.")


if __name__ == "__main__":
    main()
