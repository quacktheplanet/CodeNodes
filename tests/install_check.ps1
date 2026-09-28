# Install check: the extension zip, installed the way a user installs it, driven end to end
# through the real MCP server process by a real MCP client. Nobody presses anything in
# Blender: the add-on's link starts by itself and the MCP server finds it. Never touches your
# own Blender profile: everything goes into a throwaway folder (BLENDER_USER_RESOURCES,
# CODENODES_HOME).
#
#   powershell -File tests\install_check.ps1 -Python <python with the mcp SDK (the test client)> -Work <scratch folder>
#
# The MCP server itself is standard library only; the SDK is just the client this test drives it
# with. Setting that Python up once:  python -m venv <venv>; <venv>\Scripts\pip install "mcp[cli]" -e mcp
# The client runs twice: once with `python -m codenodes_mcp`, once the way the Claude Code plugin
# starts it (mcp\run_server.py).
param(
    [Parameter(Mandatory = $true)][string]$Python,
    [Parameter(Mandatory = $true)][string]$Work,
    [string]$Client = "mcp_e2e.py",
    # Every Blender to test, comma-separated; the last one builds the zip.
    [string[]]$Blenders = @("blender")
)
$ErrorActionPreference = "Stop"
# powershell -File passes "a,b" as one string, so split it here.
$Blenders = @($Blenders | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$Repo = Split-Path -Parent $PSScriptRoot
New-Item -ItemType Directory -Force $Work | Out-Null
$dist = Join-Path $Work "dist"
if (Test-Path $dist) { Remove-Item -Recurse -Force $dist }
New-Item -ItemType Directory $dist | Out-Null

& $Blenders[-1] --factory-startup --command extension build --source-dir "$Repo\codenodes" --output-dir $dist | Out-Null
$zip = Get-ChildItem $dist -Filter *.zip | Select-Object -First 1
if (-not $zip) { throw "the extension did not build" }
"CodeNodes test: built  $($zip.Name)"

foreach ($B in $Blenders) {
    $ver = (& $B --factory-startup --version | Select-Object -First 1).Trim()
    $tag = ($ver -replace '[^0-9.]', '')
    $prof = Join-Path $Work "profile_$tag"
    if (Test-Path $prof) { Remove-Item -Recurse -Force $prof }
    New-Item -ItemType Directory $prof | Out-Null
    $env:BLENDER_USER_RESOURCES = $prof
    $env:CODENODES_HOME = Join-Path $prof "codenodes_home"

    $valid = (& $B --factory-startup --command extension validate $zip.FullName | Select-Object -Last 1)
    "$ver  validate: $valid"
    $inst = (& $B --command extension install-file -r user_default -e $zip.FullName 2>&1 | Out-String)
    if ($inst -match "Traceback|Exception") { "$ver  FAIL install printed an error:`n$inst"; continue }
    "$ver  installed and enabled"

    $done = Join-Path $prof "done"
    $env:CODENODES_CHECK_DONE = $done
    $log = Join-Path $prof "blender.log"
    $p = Start-Process $B -ArgumentList '--python', "`"$PSScriptRoot\install_check_blender.py`"" `
        -RedirectStandardOutput $log -RedirectStandardError "$log.err" -PassThru -WindowStyle Minimized
    $ready = $false
    for ($i = 0; $i -lt 120; $i++) {
        Start-Sleep -Milliseconds 500
        if ((Test-Path $log) -and (Select-String -Path $log -Pattern 'INSTALL_CHECK (READY|FAIL)' -Quiet)) { $ready = $true; break }
    }
    Get-Content $log | Select-String 'INSTALL_CHECK' | ForEach-Object { "$ver  $($_.Line)" }
    if (-not $ready -or (Select-String -Path $log -Pattern 'INSTALL_CHECK FAIL' -Quiet)) {
        "$ver  FAIL Blender did not get ready"; $p.Kill(); continue
    }

    $out = Join-Path $prof "mcp_out"
    & $Python "$PSScriptRoot\$Client" $out | ForEach-Object { "$ver  $_" }
    if ($Client -eq "mcp_e2e.py") {
        $env:CODENODES_MCP_SCRIPT = Join-Path $Repo "mcp\run_server.py"
        "$ver  --- again, started the way the Claude Code plugin starts it ---"
        & $Python "$PSScriptRoot\$Client" "$out`_plugin" | Select-String 'server:|ALL \d+ CHECKS|FAIL' | ForEach-Object { "$ver  $($_.Line)" }
        Remove-Item Env:CODENODES_MCP_SCRIPT
    }
    New-Item -ItemType File $done | Out-Null
    if (-not $p.WaitForExit(60000)) { $p.Kill() }
    Get-Content $log | Select-String 'INSTALL_CHECK (ok|FAIL) preference' | ForEach-Object { "$ver  $($_.Line)" }
    $errs = Get-Content "$log.err" -ErrorAction SilentlyContinue | Select-String 'Traceback'
    if ($errs) { "$ver  Blender's stderr has tracebacks:"; Get-Content "$log.err" | Select-Object -Last 20 }
}
Remove-Item Env:BLENDER_USER_RESOURCES, Env:CODENODES_CHECK_DONE, Env:CODENODES_HOME -ErrorAction SilentlyContinue
