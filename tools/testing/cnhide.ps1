# Run one CodeNodes test in a windowed Blender on a hidden desktop (WinSta0\CNTests): it gets a real
# GPU context, but its window can never appear on the user's screen. Prints the result lines.
#   powershell -File cnhide.ps1 -Ver 5.1.2 -Test tests\test_gpu_nodes.py [-Extra '--factory-startup'] [-Timeout 300]
param(
    [string]$Ver = "5.1.2",
    [Parameter(Mandatory = $true)][string]$Test,
    [string]$Extra = "--factory-startup",
    [int]$Timeout = 300,
    [string]$Pattern = 'ok:|FAIL|CHECKS|Traceback|Error',
    [string]$Repo = "path/to/CodeNodes"
)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not ("HiddenRun" -as [type])) { Add-Type -Path "$here\HiddenRun.cs" }
$exe = @{ "5.0.1" = "C:/Users/you\blender-versions\blender-5.0.1-windows-x64\blender.exe";
          "5.1.2" = "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" }[$Ver]
$name = [IO.Path]::GetFileNameWithoutExtension($Test)
New-Item -ItemType Directory -Force "$env:TEMP\claude" | Out-Null
$log = "$env:TEMP\claude\cnhide_$($Ver)_$name.log"
$testPath = if ([IO.Path]::IsPathRooted($Test)) { $Test } else { "$Repo\$Test" }
$cmd = "`"$exe`" $Extra --python `"$testPath`""
$code = [HiddenRun]::Run($cmd, $Repo, $log, $Timeout * 1000)
if ($code -eq 9999) { "$Ver TIMEOUT" }
Get-Content $log | Select-String $Pattern | ForEach-Object { "$Ver $($_.Line)" }
