# Install check: the extension zip, installed the way a user installs it, driven end to end
# through the real MCP server process by a real MCP client. Never touches your own Blender
# profile: everything goes into a throwaway folder via BLENDER_USER_RESOURCES.
#
#   powershell -File tests\install_check.ps1 -Python <python with mcp + codenodes-mcp installed> -Work <scratch folder>
#
# Setting up that Python once:  python -m venv <venv>; <venv>\Scripts\pip install -e mcp
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

    $env:CODENODES_TOKEN_FILE = Join-Path $prof "config\codenodes_token.json"
    $out = Join-Path $prof "mcp_out"
    & $Python "$PSScriptRoot\$Client" $out | ForEach-Object { "$ver  $_" }
    New-Item -ItemType File $done | Out-Null
    if (-not $p.WaitForExit(60000)) { $p.Kill() }
    $errs = Get-Content "$log.err" -ErrorAction SilentlyContinue | Select-String 'Traceback'
    if ($errs) { "$ver  Blender's stderr has tracebacks:"; Get-Content "$log.err" | Select-Object -Last 20 }
}
Remove-Item Env:BLENDER_USER_RESOURCES, Env:CODENODES_CHECK_DONE, Env:CODENODES_TOKEN_FILE -ErrorAction SilentlyContinue
