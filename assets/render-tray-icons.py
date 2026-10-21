#!/usr/bin/python3
"""Generate original symbolic tray SVGs; optionally render a QA contact sheet.

SVG generation needs only Python. The contact sheet also uses the Ubuntu Cairo,
PyGObject and librsvg packages used by render-icons.py.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path


INK = "#2e3436"
TRAY = Path(__file__).resolve().parent / "tray"


def segment(x1, y1, x2, y2, width=1.5):
    """One rounded line as filled geometry using the same symbolic ink."""
    length = math.hypot(x2 - x1, y2 - y1)
    dx, dy = (y2 - y1) * width / length / 2, (x1 - x2) * width / length / 2
    points = ((x1 + dx, y1 + dy), (x2 + dx, y2 + dy),
              (x2 - dx, y2 - dy), (x1 - dx, y1 - dy))
    coordinates = " ".join(f"{x:.3f},{y:.3f}" for x, y in points)
    return (f'<polygon points="{coordinates}"/>'
            f'<circle cx="{x1:g}" cy="{y1:g}" r="{width / 2:g}"/>'
            f'<circle cx="{x2:g}" cy="{y2:g}" r="{width / 2:g}"/>')


def numpad():
    # Open the lower-right edge and keys around the integrated connection badge.
    return '''<path d="M4 1.5h10a2.5 2.5 0 0 1 2.5 2.5v5.5H15V4a1 1 0 0 0-1-1H4a1 1 0 0 0-1 1v13a1 1 0 0 0 1 1h7.5v1.5H4A2.5 2.5 0 0 1 1.5 17V4A2.5 2.5 0 0 1 4 1.5Z"/>
  <rect x="5" y="5" width="8" height="2.25" rx=".5"/>
  <rect x="5" y="9.5" width="2" height="2" rx=".45"/>
  <rect x="9" y="9.5" width="2" height="2" rx=".45"/>
  <rect x="13" y="9.5" width="2" height="2" rx=".45"/>
  <rect x="5" y="13.5" width="2" height="2" rx=".45"/>
  <rect x="9" y="13.5" width="2" height="2" rx=".45"/>'''


def badge(transport):
    if transport == "usb":
        return "\n  ".join((
            '<path d="m18 10 2 3h-4Z"/>', segment(18, 12.5, 18, 20),
            segment(14.2, 14.1, 14.2, 15.2), segment(14.2, 15.2, 18, 17.8),
            segment(21.1, 14.8, 21.1, 16.3), segment(21.1, 16.3, 18, 18.4),
            '<circle cx="14.2" cy="13.5" r="1.3"/>',
            '<rect x="19.8" y="12.5" width="2.6" height="2.6" rx=".15"/>',
            '<circle cx="18" cy="20.3" r="1.45"/>'))
    if transport == "bluetooth":
        return "\n  ".join((
            segment(17.3, 10.5, 17.3, 21), segment(17.3, 10.5, 21.2, 13.8),
            segment(21.2, 13.8, 14.2, 19.1), segment(14.2, 12.4, 21.2, 17.8),
            segment(21.2, 17.8, 17.3, 21)))
    if transport == "offline":
        return "\n  ".join((segment(14.5, 13, 21, 19.5), segment(21, 13, 14.5, 19.5)))
    if transport == "ambiguous":
        return '''<path fill-rule="evenodd" d="M18 10 24 21H12Zm0 3.2L14.7 19.5h6.6Z"/>
  <rect x="17.35" y="14.4" width="1.3" height="2.65" rx=".55"/>
  <circle cx="18" cy="18.25" r=".75"/>'''
    if transport == "unknown":
        return '''<path d="M14.6 13.55c0-2.08 1.38-3.3 3.53-3.3 2.1 0 3.57 1.12 3.57 3.06 0 1.58-1.02 2.3-2.15 3.05-.88.59-1.15 1.04-1.15 2.04h-1.6c0-1.58.37-2.27 1.55-3.06 1.05-.7 1.7-1.1 1.7-2.03 0-1.04-.77-1.57-1.92-1.57-1.26 0-1.93.59-1.93 1.81Z"/>
  <circle cx="17.6" cy="20.5" r="1"/>'''
    raise ValueError(f"Unknown transport: {transport}")


def battery(percent):
    if percent is None:
        return '''<g opacity=".6">
    <rect x="1.5" y="22" width="2.5" height="1.5" rx=".5"/>
    <rect x="5.25" y="22" width="2.5" height="1.5" rx=".5"/>
    <rect x="9" y="22" width="2.5" height="1.5" rx=".5"/>
    <rect x="12.75" y="22" width="1.75" height="1.5" rx=".5"/>
  </g>'''
    track = '<rect x="1.5" y="21.5" width="13" height="2" rx="1" opacity=".28"/>'
    if not percent:
        return track
    # A clip preserves the 5% sliver and the rounded edge of the full track.
    return (track + '\n  <clipPath id="battery-track">'
            '<rect x="1.5" y="21.5" width="13" height="2" rx="1"/></clipPath>'
            f'\n  <rect x="1.5" y="21.5" width="{13 * percent / 100:g}" height="2"'
            ' clip-path="url(#battery-track)"/>')


def svg(transport, percent=None):
    detail = "Battery unavailable" if percent is None else f"Battery {percent}%"
    descriptions = {"usb": "USB connected", "bluetooth": "Bluetooth connected",
                    "offline": "Not connected", "ambiguous": "Multiple numpads detected",
                    "unknown": "Connection unknown"}
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="{INK}">
  <title>OpenGMMK Numpad Editor</title>
  <desc>{descriptions[transport]}; {detail.lower()}. No charging state is implied.</desc>
  {numpad()}
  {badge(transport)}
  {battery(percent)}
</svg>
'''


def artwork():
    result = {}
    for transport in ("usb", "bluetooth"):
        for percent in range(0, 101, 5):
            result[f"gmmk-numpad-{transport}-{percent:03d}-symbolic.svg"] = svg(transport, percent)
        result[f"gmmk-numpad-{transport}-unavailable-symbolic.svg"] = svg(transport)
    for transport in ("offline", "ambiguous", "unknown"):
        result[f"gmmk-numpad-{transport}-symbolic.svg"] = svg(transport)
    return result


def contact_sheet(path):
    import cairo
    import gi
    gi.require_version("Rsvg", "2.0")
    from gi.repository import Rsvg

    columns = (("usb", 100), ("bluetooth", 100), ("usb", 60), ("bluetooth", 15),
               ("usb", 0), ("bluetooth", None), ("offline", None),
               ("ambiguous", None), ("unknown", None))
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 900, 280)
    context = cairo.Context(surface)
    for row, (background, ink) in enumerate((("#242424", "#f5f5f5"), ("#fafafa", "#303030"))):
        rgb = tuple(int(background[index:index + 2], 16) / 255 for index in (1, 3, 5))
        context.set_source_rgb(*rgb)
        context.rectangle(0, row * 140, 900, 140)
        context.fill()
        for column, (transport, percent) in enumerate(columns):
            icon = Rsvg.Handle.new_from_data(svg(transport, percent).replace(INK, ink).encode())
            for size, y in ((22, row * 140 + 13), (60, row * 140 + 50)):
                viewport = Rsvg.Rectangle()
                viewport.x, viewport.y = column * 100 + (100 - size) / 2, y
                viewport.width = viewport.height = size
                icon.render_document(context, viewport)
            rgb = tuple(int(ink[index:index + 2], 16) / 255 for index in (1, 3, 5))
            context.set_source_rgb(*rgb)
            context.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            context.set_font_size(10)
            text = transport if percent is None else f"{transport} {percent}%"
            label_width = context.text_extents(text).width
            context.move_to(column * 100 + (100 - label_width) / 2, row * 140 + 129)
            context.show_text(text)
    surface.write_to_png(str(path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check tracked SVGs without writing")
    parser.add_argument("--contact-sheet", type=Path, help="Optional PNG path for visual verification")
    args = parser.parse_args()
    generated = artwork()
    if args.check:
        mismatched = [name for name, source in generated.items()
                      if not (TRAY / name).is_file() or (TRAY / name).read_text() != source]
        if mismatched:
            parser.exit(1, "Tray artwork needs regeneration: " + ", ".join(mismatched) + "\n")
    else:
        TRAY.mkdir(exist_ok=True)
        for name, source in generated.items():
            (TRAY / name).write_text(source)
    print(f"{'Checked' if args.check else 'Generated'} {len(generated)} symbolic tray SVGs")
    if args.contact_sheet:
        contact_sheet(args.contact_sheet)
        print(args.contact_sheet)


if __name__ == "__main__":
    main()
