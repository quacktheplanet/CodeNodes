# Every CodeNodes suite on both Blender versions.
# Suites that need the GPU run windowed on a hidden desktop (WinSta0\CNTests): a real GPU context,
# but no window ever appears on the user's screen. Everything else runs in background mode (-b).
param([string]$Repo = "path/to/CodeNodes", [string[]]$Only = @())
Add-Type -Path "$PSScriptRoot\HiddenRun.cs"
$py = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
$env:PYTHONPATH = "C:/Users/you\blender-versions\deps-py311"
$T = "$Repo\tests"
New-Item -ItemType Directory -Force "$env:TEMP\claude" | Out-Null
function Want($s) { return ($Only.Count -eq 0) -or ($Only -contains $s) }
foreach ($s in 'test_mesher.py', 'test_graph.py', 'test_rpc.py', 'test_shapes.py', 'test_shape_js.py', 'test_factory.py', 'test_decl.py') {
    if (-not (Want $s) -or -not (Test-Path "$T\$s")) { continue }
    $out = & $py "$T\$s" 2>&1 | Select-Object -Last 1
    "{0,-22} {1}" -f $s, $out
}
$versions = @(
    @("5.0.1", "C:/Users/you\blender-versions\blender-5.0.1-windows-x64\blender.exe"),
    @("5.1.2", "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe")
)
$windowed = 'test_blender.py', 'test_nodes.py', 'test_bake.py', 'test_volume.py', 'test_particles.py', 'test_agent.py',
            'test_server.py', 'test_shape_blender.py', 'test_bake_nodes.py', 'test_gn_link.py', 'test_link.py',
            'test_gpu_nodes.py', 'test_modular.py', 'test_graph_chains.py', 'test_gpu_cache.py', 'test_lights.py', 'test_live_preview.py', 'test_galaxy.py', 'test_render_f12.py',
            'test_uses.py', 'test_lists.py', 'test_explode.py', 'test_groups.py', 'test_explode_ui.py'
$background = 'test_gn.py', 'test_gn_library.py', 'test_web.py', 'test_farm.py', 'test_factory_blender.py',
              'test_geonodes_library.py', 'test_node_reference.py', 'test_render_form.py'
foreach ($v in $versions) {
    foreach ($s in $windowed) {
        if (-not (Want $s) -or -not (Test-Path "$T\$s")) { continue }
        $log = "$env:TEMP\claude\all_$($v[0])_$s.log"
        $code = [HiddenRun]::Run("`"$($v[1])`" --factory-startup --enable-event-simulate --python `"$T\$s`"", $Repo, $log, 900000)
        if ($code -eq 9999) { $res = "TIMEOUT" }
        else { $res = ((Get-Content $log | Select-String 'ALL \d+ CHECKS|FAIL') | ForEach-Object { $_.Line }) -join ' | ' }
        "{0} {1,-22} {2}" -f $v[0], $s, $res
    }
    foreach ($s in $background) {
        if (-not (Want $s) -or -not (Test-Path "$T\$s")) { continue }
        $res = & $v[1] -b --factory-startup --python "$T\$s" 2>&1 | Select-String 'ALL \d+ CHECKS|^FAIL' | ForEach-Object { $_.Line }
        "{0} {1,-22} {2}" -f $v[0], $s, ($res -join ' | ')
    }
}
