#!/usr/bin/env bash
# Build the CodeNodes extension zip and install it the way a user would, into a throwaway profile, on
# every Blender listed; then check the installed add-on loads, its operators exist, and (on 5.2+, which
# has the GPU without a window) that a chain with a use line, a List and Explode runs from the install.
#
#   tools/testing/install_check_linux.sh <blender> [blender ...]
set -u
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
WORK="${TMPDIR:-/tmp}/codenodes_install"
rm -rf "$WORK"; mkdir -p "$WORK/dist"
B0="$1"
"$B0" -b --factory-startup --command extension build --source-dir "$REPO/codenodes" --output-dir "$WORK/dist" \
    > "$WORK/build.log" 2>&1
ZIP=$(ls "$WORK"/dist/*.zip 2>/dev/null | head -1)
[ -n "$ZIP" ] || { echo "build FAILED (see $WORK/build.log)"; exit 1; }
echo "built $(basename "$ZIP") ($(du -h "$ZIP" | cut -f1))"
for B in "$@"; do
    V=$("$B" -b --factory-startup --python-expr "import bpy; print('CNVER', bpy.app.version_string)" 2>/dev/null \
        | awk '/^CNVER/ {print $2}')
    PROFILE="$WORK/profile_$V"; mkdir -p "$PROFILE"
    export BLENDER_USER_RESOURCES="$PROFILE"
    "$B" -b --command extension install-file -r user_default -e "$ZIP" > "$WORK/install_$V.log" 2>&1
    "$B" -b --python "$REPO/tests/install_check_linux.py" > "$WORK/check_$V.log" 2>&1
    R=$(grep -aE "ALL [0-9]+ CHECKS|^FAIL" "$WORK/check_$V.log" | head -1)
    printf "%-8s %s\n" "$V" "${R:-no result (see $WORK/check_$V.log)}"
    unset BLENDER_USER_RESOURCES
done
