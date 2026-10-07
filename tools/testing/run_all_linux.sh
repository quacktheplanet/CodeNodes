#!/usr/bin/env bash
# Every CodeNodes suite on Linux, no display needed.
#   - plain Python suites (no Blender)
#   - background suites (-b) on every Blender listed
#   - GPU suites in background mode through tests/headless.py, on Blender 5.2 or later only (gpu.init()
#     starts the GPU without a window; earlier versions have no GPU in -b)
# Suites that draw the live viewport (or press keys, or undo) still need a window: on Windows use
# run_all_cn.ps1; they are listed under WINDOW_ONLY and skipped here.
#
#   tools/testing/run_all_linux.sh [-o suite]... [blender ...]
#   BLENDERS="/opt/blender-5.1.2/blender /opt/blender-5.2.2/blender" tools/testing/run_all_linux.sh
#   GPU_BACKEND=vulkan tools/testing/run_all_linux.sh ...   (Blender's --gpu-backend; default: Blender's own)
set -u
BACKEND_ARGS=()
[ -n "${GPU_BACKEND:-}" ] && BACKEND_ARGS=(--gpu-backend "$GPU_BACKEND")
ENV_BLENDERS="${BLENDERS:-blender}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
T="$REPO/tests"
ONLY=()
while [ $# -gt 0 ] && [ "$1" = "-o" ]; do ONLY+=("$2"); shift 2; done
BLENDERS=("$@")
[ ${#BLENDERS[@]} -eq 0 ] && read -r -a BLENDERS <<< "$ENV_BLENDERS"
LOGS="${TMPDIR:-/tmp}/codenodes_tests"; mkdir -p "$LOGS"
PY="${PYTHON:-python3}"

PLAIN="test_mesher test_graph test_rpc test_shapes test_shape_js test_factory test_decl"
BACKGROUND="test_gn test_gn_library test_web test_factory_blender test_geonodes_library test_node_reference test_lists test_explode"
# test_bake saves the .blend that test_farm opens, so it runs first
HEADLESS="test_bake test_blender test_volume test_shape_blender test_particles test_galaxy test_bake_nodes test_gn_link
          test_nodes test_agent test_server test_link"
GPU_BACKGROUND="test_cli_render test_uses test_groups"      # plain -b scripts that use the GPU
WINDOW_ONLY="test_gpu_nodes test_modular test_graph_chains test_live_preview test_render_f12"

want() { [ ${#ONLY[@]} -eq 0 ] && return 0; for o in "${ONLY[@]}"; do [ "$o" = "$1" ] && return 0; done; return 1; }
result() { grep -aiE "ALL [0-9]+ CHECKS|^FAIL|checks passed" "$1" | head -2 | tr '\n' ' '; }

for s in $PLAIN; do
    want "$s" && [ -f "$T/$s.py" ] || continue
    printf "%-8s %-24s %s\n" python "$s" "$("$PY" "$T/$s.py" 2>&1 | tail -1)"
done
for B in "${BLENDERS[@]}"; do
    V=$("$B" -b --factory-startup --python-expr "import bpy; print('CNVER', bpy.app.version_string)" 2>/dev/null \
        | awk '/^CNVER/ {print $2}')
    [ -n "$V" ] || { echo "$B: not a working Blender"; continue; }
    run() {   # run <label> <suite> <args...>
        local log="$LOGS/${V}${GPU_BACKEND:+_$GPU_BACKEND}_$2.log"
        timeout 900 "$B" -b --factory-startup ${BACKEND_ARGS[@]+"${BACKEND_ARGS[@]}"} "${@:3}" > "$log" 2>&1
        local r; r=$(result "$log"); printf "%-8s %-24s %s\n" "$V" "$2" "${r:-no result (see $log)}"
    }
    for s in $BACKGROUND; do
        want "$s" && [ -f "$T/$s.py" ] && run bg "$s" --python "$T/$s.py"
    done
    if "$B" -b --factory-startup --python-expr "import gpu, sys; sys.exit(0 if hasattr(gpu, 'init') else 1)" \
            > /dev/null 2>&1; then
        for s in $HEADLESS; do
            want "$s" && [ -f "$T/$s.py" ] && run gpu "$s" --python "$T/headless.py" -- "$T/$s.py"
        done
        want test_farm && run bg test_farm --python "$T/test_farm.py"
        for s in $GPU_BACKGROUND; do
            want "$s" && [ -f "$T/$s.py" ] && run gpu "$s" --python "$T/$s.py"
        done
    else
        printf "%-8s %s\n" "$V" "GPU suites skipped: background mode has no GPU before Blender 5.2"
    fi
done
