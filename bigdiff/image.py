"""Pixel diff: how much of an image changed, where, and a heatmap to look at.

For UI checks (Playwright screenshots before/after a change) and for any two images. A small
per-channel threshold keeps anti-aliasing noise from counting as change.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from PIL import Image, ImageChops

DEFAULT_THRESHOLD = 16
Source = str | Path | BinaryIO  # a path, or a binary stream (Pillow reads and writes both)


@dataclass
class ImageDiff:
    size_a: tuple[int, int]
    size_b: tuple[int, int]
    changed_px: int
    total_px: int
    bbox: tuple[int, int, int, int] | None  # left, top, right, bottom of the changed area

    @property
    def fraction(self) -> float:
        return self.changed_px / self.total_px if self.total_px else 0.0

    def render(self) -> str:
        if self.size_a != self.size_b:
            return f"sizes differ: {self.size_a} → {self.size_b} (compared on the common area); {self.fraction:.2%} changed"
        where = f", within box {self.bbox}" if self.bbox else ""
        return f"{self.changed_px:,} of {self.total_px:,} px changed ({self.fraction:.2%}){where}"


def diff(a_src: Source, b_src: Source, threshold: int = DEFAULT_THRESHOLD, heatmap: Source | None = None) -> ImageDiff:
    a = Image.open(a_src).convert("RGBA")
    b = Image.open(b_src).convert("RGBA")
    w, h = min(a.width, b.width), min(a.height, b.height)
    ca, cb = a.crop((0, 0, w, h)), b.crop((0, 0, w, h))
    # max channel difference per pixel, then threshold to a 0/255 mask
    delta = ImageChops.difference(ca, cb).split()
    mx = delta[0]
    for ch in delta[1:]:
        mx = ImageChops.lighter(mx, ch)
    mask = mx.point(lambda v: 255 if v > threshold else 0)
    changed = mask.histogram()[255]
    if heatmap is not None:
        base = cb.convert("L").convert("RGBA")
        base = Image.blend(base, Image.new("RGBA", base.size, (255, 255, 255, 255)), 0.6)
        red = Image.new("RGBA", base.size, (230, 30, 30, 255))
        base.paste(red, mask=mask)
        base.convert("RGB").save(heatmap, format="PNG")
    return ImageDiff(a.size, b.size, changed, w * h, mask.getbbox())
