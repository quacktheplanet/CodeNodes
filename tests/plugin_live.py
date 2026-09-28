"""The Claude Code plugin, end to end: install it from this repo into a throwaway Claude config,
then have a real headless Claude do a small job in a real Blender through it.

    python tests/plugin_live.py --blender <blender.exe> [--model sonnet]

1. `claude plugin marketplace add <repo>` + `claude plugin install codenodes@codenodes` with
   CLAUDE_CONFIG_DIR pointed at a scratch folder (your own Claude setup is not touched), and
   `claude plugin list` shows it.
2. Blender opens minimised with CodeNodes loaded from this repo (its link starts by itself; a
   scratch CODENODES_HOME keeps the registration private to this run).
3. `claude -p` runs with the plugin (--plugin-dir, using your normal login), asked in plain words
   for a code node; the scene is then checked through the link: the object exists, it has a code
   node, and the input Claude was asked to set has that value.

Costs a few cents of Claude usage. Prints ok / FAIL lines and "ALL n CHECKS PASSED".
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "mcp"))
from codenodes_mcp.connection import Connection, instances  # noqa: E402

TOOL_PREFIX = "mcp__plugin_codenodes_codenodes__"
PROMPT = ("In my open Blender, use CodeNodes to add a vase as a code node (the Vase template) and set "
          "its height input to 0.6. Name the object 'Tall Vase'. Then look at it and tell me in one "
          "sentence what you made.")
checks = 0
failed = False


def check(cond, msg):
    global checks, failed
    checks += 1
    print(("  ok: " if cond else "FAIL: ") + msg, flush=True)
    if not cond:
        failed = True


def claude_exe():
    return shutil.which("claude") or shutil.which("claude.exe")


def install_check(work):
    env = dict(os.environ, CLAUDE_CONFIG_DIR=os.path.join(work, "claude-config"))
    exe = claude_exe()
    add = subprocess.run([exe, "plugin", "marketplace", "add", ROOT], env=env, capture_output=True,
                         text=True, encoding="utf-8", errors="replace", timeout=300)
    check(add.returncode == 0, f"marketplace add from the repo ({(add.stdout + add.stderr).strip()[-120:]})")
    inst = subprocess.run([exe, "plugin", "install", "codenodes@codenodes"], env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=300)
    check(inst.returncode == 0, f"plugin install codenodes@codenodes ({(inst.stdout + inst.stderr).strip()[-120:]})")
    lst = subprocess.run([exe, "plugin", "list"], env=env, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=120)
    check("codenodes" in lst.stdout, "claude plugin list shows it")


def run_claude(prompt, env, workdir, model=None):
    cmd = [claude_exe(), "-p", prompt, "--plugin-dir", ROOT, "--output-format", "stream-json",
           "--verbose", "--max-turns", "30", "--allowedTools", f"{TOOL_PREFIX}*", "Skill", "Read"]
    if model:
        cmd += ["--model", model]
    proc = subprocess.run(cmd, cwd=workdir, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=1200)
    tools, skills, reply, cost = [], [], "", None
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") == "assistant":
            for block in ev.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    tools.append(block["name"])
                    if block["name"] == "Skill":
                        skills.append(str(block.get("input", {}).get("skill")))
        elif ev.get("type") == "result":
            reply = ev.get("result") or ""
            cost = ev.get("total_cost_usd")
    return {"tools": tools, "skills": skills, "reply": reply, "cost": cost,
            "stderr": proc.stderr[-1500:], "code": proc.returncode}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blender", required=True)
    ap.add_argument("--model")
    args = ap.parse_args()
    work = tempfile.mkdtemp(prefix="codenodes_plugin_")
    install_check(work)

    env = dict(os.environ, CODENODES_HOME=os.path.join(work, "home"), CODENODES_LINK="1",
               CODENODES_CHECK_DONE=os.path.join(work, "done"), CODENODES_KEEP_OPEN_S="1500")
    os.environ["CODENODES_HOME"] = env["CODENODES_HOME"]
    si = None
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 7                         # SW_SHOWMINNOACTIVE
    log = open(os.path.join(work, "blender.log"), "w", encoding="utf-8")
    blender = subprocess.Popen([args.blender, "--factory-startup", "--python",
                                os.path.join(ROOT, "tests", "keep_open_blender.py")],
                               env=env, stdout=log, stderr=subprocess.STDOUT, startupinfo=si)
    try:
        deadline = time.time() + 90
        while time.time() < deadline and not instances():
            time.sleep(0.5)
        check(bool(instances()), "Blender opened and its link registered by itself")
        workdir = os.path.join(work, "claude-workdir")
        os.makedirs(workdir, exist_ok=True)
        run = run_claude(PROMPT, env, workdir, args.model)
        used = sorted({t.replace(TOOL_PREFIX, "") for t in run["tools"] if t.startswith(TOOL_PREFIX)})
        print(f"  Claude used: {used}; skills {run['skills']}; ${run['cost']}", flush=True)
        print(f"  Claude said: {run['reply'][:300]}", flush=True)
        if run["code"] != 0:
            print(run["stderr"])
        check(bool(used), "Claude drove CodeNodes through the plugin's MCP tools")
        check("code_node" in used, "and used code_node, as the skill says to for a code node")
        c = Connection(timeout=120)
        scene = c.call("scene")
        names = json.dumps(scene, ensure_ascii=False)
        check('"Tall Vase"' in names, "an object called exactly 'Tall Vase' exists")
        # code_node on a missing object would make a new one, so only ask once it's there
        got = c.call("code_node", object="Tall Vase") if '"Tall Vase"' in names else {"error": "missing"}
        c.close()
        check(got.get("ok") and got.get("tree"), f"'Tall Vase' exists with a code node ({got.get('error', got.get('node'))})")
        height = (got.get("inputs") or {}).get("height")
        check(height is not None and abs(height - 0.6) < 1e-4, f"and its height input is 0.6 ({height})")
    finally:
        open(env["CODENODES_CHECK_DONE"], "w").close()
        try:
            blender.wait(60)
        except subprocess.TimeoutExpired:
            blender.kill()
        log.close()
    print(f"\n{'FAIL' if failed else f'ALL {checks} CHECKS PASSED'}", flush=True)


if __name__ == "__main__":
    main()
