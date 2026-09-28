# Two Blenders open at once: each CodeNodes link takes its own port and registers itself, and
# the MCP connection lists both and can talk to either. Uses a throwaway CODENODES_HOME.
#
#   powershell -File tests\two_blenders.ps1 -Blenders <blender 1>,<blender 2> [-Python python]
param(
    [Parameter(Mandatory = $true)][string[]]$Blenders,
    [string]$Python = "python"
)
$ErrorActionPreference = "Stop"
$Blenders = @($Blenders | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$Repo = Split-Path -Parent $PSScriptRoot
$work = Join-Path $env:TEMP ("codenodes_two_" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory $work | Out-Null
$env:CODENODES_HOME = Join-Path $work "home"
$env:CODENODES_LINK = "1"
$env:CODENODES_CHECK_DONE = Join-Path $work "done"
$procs = @()
$i = 0
foreach ($B in $Blenders) {
    $i++
    $log = Join-Path $work "blender$i.log"
    $procs += Start-Process $B -ArgumentList '--factory-startup', '--python', "`"$PSScriptRoot\keep_open_blender.py`"" `
        -RedirectStandardOutput $log -RedirectStandardError "$log.err" -PassThru -WindowStyle Minimized
    Start-Sleep -Milliseconds 300
}
$client = @'
import os, sys, time
sys.path.insert(0, os.path.join(sys.argv[1], "mcp"))
from codenodes_mcp.connection import Connection, instances
want = int(sys.argv[2])
deadline = time.time() + 90
live = []
while time.time() < deadline:
    live = instances()
    if len(live) >= want:
        break
    time.sleep(0.5)
ports = sorted(i["port"] for i in live)
print(f"{'ok' if len(live) == want and len(set(ports)) == want else 'FAIL'}: {len(live)} Blenders registered on ports {ports}")
seen = set()
for info in live:
    c = Connection(port=info["port"], timeout=60)
    st = c.call("status")
    c.close()
    seen.add((st["port"], info["pid"]))
    print(f"ok: port {st['port']} answers as Blender {st['blender']} (pid {info['pid']})")
default = Connection(timeout=60)
st = default.call("status")
default.close()
print(f"ok: with no port chosen the connection picks the newest ({st['port']})")
print("ALL CHECKS PASSED" if len(seen) == want else "FAIL: not every Blender answered")
'@
$clientPath = Join-Path $work "client.py"
Set-Content -Path $clientPath -Value $client -Encoding utf8
& $Python $clientPath $Repo $Blenders.Count
New-Item -ItemType File $env:CODENODES_CHECK_DONE | Out-Null
foreach ($p in $procs) { if (-not $p.WaitForExit(30000)) { $p.Kill() } }
$left = @(Get-ChildItem (Join-Path $env:CODENODES_HOME "instances") -Filter *.json -ErrorAction SilentlyContinue)
if ($left.Count -eq 0) { "ok: both Blenders removed their registration on quit" } else { "FAIL: $($left.Count) registrations left behind" }
Remove-Item Env:CODENODES_HOME, Env:CODENODES_LINK, Env:CODENODES_CHECK_DONE -ErrorAction SilentlyContinue
