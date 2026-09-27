"""A plan (and its equipment) drawn as a PNG, in pure Python — for looking at a plan before
anything is built, and for tests. North is up.

    png_bytes = draw(plan, layout=None, px_per_m=10, level=0)
    save(path, plan, layout=None, ...)
"""

from __future__ import annotations

import struct
import zlib

ZONE = {
    "production": (222, 232, 242), "storage": (232, 226, 214), "dock": (214, 232, 216),
    "shipping": (214, 232, 216), "qa": (238, 222, 238), "office": (246, 240, 214),
    "amenity": (236, 236, 222), "utility": (226, 226, 226), "lab": (226, 236, 246),
    "retail": (246, 228, 214), "living": (246, 240, 214), "kitchen": (240, 232, 214),
    "bedroom": (232, 238, 248), "bath": (218, 236, 240), "room": (238, 238, 232),
    "stair": (220, 220, 220),
}
KIND = {
    "rack": (40, 80, 160), "cnc": (120, 130, 140), "robot_arm": (240, 110, 20), "fence": (230, 190, 20),
    "conveyor": (90, 90, 90), "workbench": (150, 105, 60), "pallet": (180, 140, 90), "forklift": (230, 180, 20),
    "amr": (60, 160, 90), "cabinet": (110, 110, 100), "desk": (160, 160, 160), "tank": (150, 160, 170),
}
AISLE = (250, 250, 250)
WALL = (40, 40, 44)
BG = (255, 255, 255)


class Canvas:
    def __init__(self, w, h, bg=BG):
        self.w, self.h = w, h
        self.px = bytearray(bytes(bg) * (w * h))

    def rect(self, x0, y0, x1, y1, color):
        x0, x1 = int(round(min(x0, x1))), int(round(max(x0, x1)))
        y0, y1 = int(round(min(y0, y1))), int(round(max(y0, y1)))
        x1, y1 = max(x1, x0 + 1), max(y1, y0 + 1)          # thin parts stay visible
        x0, x1 = max(0, x0), min(self.w, x1)
        y0, y1 = max(0, y0), min(self.h, y1)
        if x1 <= x0 or y1 <= y0:
            return
        row = bytes(color) * (x1 - x0)
        for y in range(y0, y1):
            i = (y * self.w + x0) * 3
            self.px[i:i + len(row)] = row

    def outline(self, x0, y0, x1, y1, color, t=1):
        self.rect(x0, y0, x1, y0 + t, color)
        self.rect(x0, y1 - t, x1, y1, color)
        self.rect(x0, y0, x0 + t, y1, color)
        self.rect(x1 - t, y0, x1, y1, color)

    def png(self):
        raw = b"".join(b"\x00" + bytes(self.px[y * self.w * 3:(y + 1) * self.w * 3]) for y in range(self.h))

        def chunk(tag, data):
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def draw(plan, layout=None, px_per_m=10, level=0, margin_m=2.0):
    W, D = plan["site"]["size"]
    s = px_per_m
    m = margin_m * s
    c = Canvas(int(W * s + 2 * m), int(D * s + 2 * m))

    def X(x):
        return m + x * s

    def Y(y):               # north up
        return m + (D - y) * s

    def R(r, color):
        c.rect(X(r[0]), Y(r[3]), X(r[2]), Y(r[1]), color)

    lvl = next(l for l in plan["levels"] if l["level"] == level)
    for sp in lvl["spaces"]:
        R(sp["rect"], ZONE.get(sp["type"], (235, 235, 235)))
    for a in lvl["aisles"]:
        R(a["rect"], AISLE)
        r = a["rect"]
        # yellow lines along the aisle edges
        if r[2] - r[0] > r[3] - r[1]:
            R([r[0], r[1], r[2], r[1] + 0.12], (230, 190, 20))
            R([r[0], r[3] - 0.12, r[2], r[3]], (230, 190, 20))
        else:
            R([r[0], r[1], r[0] + 0.12, r[3]], (230, 190, 20))
            R([r[2] - 0.12, r[1], r[2], r[3]], (230, 190, 20))
    for st in lvl["stairs"]:
        R(st["rect"], (200, 200, 205))
    if layout:
        for ra in layout.get("rack_aisles", []):
            if any(it["space"] == ra["space"] and it["level"] == level for it in layout["items"]):
                R(ra["rect"], (245, 245, 238))
        for it in layout["items"]:
            if it["level"] != level:
                continue
            col = KIND.get(it["kind"], (100, 100, 100))
            if it["kind"] == "fence" or it.get("overhead"):
                for sd in it["solids"]:
                    R(sd, col)
                if it.get("overhead"):
                    f = it["footprint"]
                    c.outline(X(f[0]), Y(f[3]), X(f[2]), Y(f[1]), col, 1)
                continue
            R(it["footprint"], col)
            f = it["footprint"]
            c.outline(X(f[0]), Y(f[3]), X(f[2]), Y(f[1]), (30, 30, 30), 1)
    t_ext = max(2, int(plan["spec"]["rules"]["wall_exterior"] * s))
    t_int = max(1, int(plan["spec"]["rules"]["wall_interior"] * s))
    for w in lvl["walls"]:
        x0, y0, x1, y1 = w
        if abs(y0 - y1) < 1e-6:
            c.rect(X(min(x0, x1)) - t_int / 2, Y(y0) - t_int / 2, X(max(x0, x1)) + t_int / 2, Y(y0) + t_int / 2, WALL)
        else:
            c.rect(X(x0) - t_int / 2, Y(max(y0, y1)) - t_int / 2, X(x0) + t_int / 2, Y(min(y0, y1)) + t_int / 2, WALL)
    c.rect(X(0) - t_ext, Y(D) - t_ext, X(W) + t_ext, Y(D), WALL)
    c.rect(X(0) - t_ext, Y(0), X(W) + t_ext, Y(0) + t_ext, WALL)
    c.rect(X(0) - t_ext, Y(D), X(0), Y(0), WALL)
    c.rect(X(W), Y(D), X(W) + t_ext, Y(0), WALL)
    colors = {"door": (255, 255, 255), "double_door": (255, 255, 255), "opening": (255, 255, 255),
              "exit": (40, 200, 60), "drive_in": (40, 120, 220), "dock": (40, 120, 220), "window": (150, 210, 240)}
    for o in lvl["openings"]:
        x, y = o["pos"]
        hw = o["width"] / 2
        th = (t_ext if o["wall"] == "exterior" else t_int) / s + 0.1
        r = [x - hw, y - th, x + hw, y + th] if o["axis"] == "x" else [x - th, y - hw, x + th, y + hw]
        R(r, colors.get(o["kind"], (255, 255, 255)))
    if level == 0:
        for col in plan["columns"]:
            R([col[0] - 0.2, col[1] - 0.2, col[0] + 0.2, col[1] + 0.2], (20, 20, 20))
    return c.png()


def save(path, plan, layout=None, px_per_m=10, level=0):
    data = draw(plan, layout, px_per_m, level)
    with open(path, "wb") as f:
        f.write(data)
    return path
