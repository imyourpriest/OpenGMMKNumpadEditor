#!/usr/bin/python3
"""Export the original scalable app icon using Ubuntu's librsvg renderer."""

from pathlib import Path

import cairo
import gi

gi.require_version("Rsvg", "2.0")
from gi.repository import Rsvg  # noqa: E402


def main():
    root = Path(__file__).resolve().parent
    name = "io.github.opengmmk.NumpadEditor"
    icon = Rsvg.Handle.new_from_file(str(root / f"{name}.svg"))
    for size in (16, 24, 32, 48, 64, 128, 256, 512):
        output = root / "icons" / f"{size}x{size}" / f"{name}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        context = cairo.Context(surface)
        viewport = Rsvg.Rectangle()
        viewport.x, viewport.y = 0, 0
        viewport.width, viewport.height = size, size
        icon.render_document(context, viewport)
        surface.write_to_png(str(output))
        print(output.relative_to(root))


if __name__ == "__main__":
    main()
