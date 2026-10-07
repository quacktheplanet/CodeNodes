#!/usr/bin/env bash
# A virtual display for the window-only suites on a Linux machine with no X server and no root:
# downloads Xvfb from the distribution (apt-get download, no install), unpacks it into a cache folder,
# points its keymap compiler at the unpacked one, and starts it. Then run the suites with a real GPU
# through Blender's Vulkan backend (it presents into the virtual display):
#
#   eval "$(tools/testing/xvfb_linux.sh)"            # prints: export DISPLAY=:77
#   GPU_BACKEND=vulkan tools/testing/run_all_linux.sh /path/to/blender-5.2/blender
#
# Ubuntu/Debian only (apt-get download, dpkg -x). Without Vulkan, Blender falls back to Mesa's software
# OpenGL (llvmpipe), which works but is slow.
set -eu
NUM="${XVFB_DISPLAY:-77}"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/codenodes-xvfb"
ROOT="$CACHE/root"
XKBDIR="/tmp/xkb"                    # same length as /usr/bin: the path compiled into Xvfb is patched to it
if [ ! -x "$CACHE/Xvfb" ]; then
    mkdir -p "$CACHE" && cd "$CACHE"
    apt-get download xvfb x11-xkb-utils xkb-data libxfont2 libfontenc1 xserver-common libxkbfile1 >&2
    for d in *.deb; do dpkg -x "$d" "$ROOT"; done
    python3 - "$ROOT/usr/bin/Xvfb" "$CACHE/Xvfb" <<'PY'
import sys
d = open(sys.argv[1], "rb").read()
assert d.count(b"/usr/bin\x00") == 1, "unexpected Xvfb build"
open(sys.argv[2], "wb").write(d.replace(b"/usr/bin\x00", b"/tmp/xkb\x00"))
PY
    chmod +x "$CACHE/Xvfb"
fi
mkdir -p "$XKBDIR"
cat > "$XKBDIR/xkbcomp" <<SH
#!/bin/sh
LD_LIBRARY_PATH=$ROOT/usr/lib/x86_64-linux-gnu exec $ROOT/usr/bin/xkbcomp "\$@"
SH
chmod +x "$XKBDIR/xkbcomp"
if [ ! -e "/tmp/.X11-unix/X$NUM" ]; then
    LD_LIBRARY_PATH="$ROOT/usr/lib/x86_64-linux-gnu" nohup "$CACHE/Xvfb" ":$NUM" -screen 0 1600x1000x24 \
        -xkbdir "$ROOT/usr/share/X11/xkb" -nolisten tcp > "$CACHE/xvfb.log" 2>&1 &
    for _ in $(seq 50); do [ -e "/tmp/.X11-unix/X$NUM" ] && break; sleep 0.1; done
fi
echo "export DISPLAY=:$NUM"
