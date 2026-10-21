# Application icon

The original OpenGMMK Numpad Editor icon depicts a number pad with its slider,
rotary dial, a highlighted key, and an RGB accent. The navy tile and teal key
remain readable against light and dark desktops.

The scalable source is `io.github.opengmmk.NumpadEditor.svg`. The application
and installer can use that SVG directly. Portable bundles also include PNG
exports in 16, 24, 32, 48, 64, 128, 256, and 512 pixel sizes.

To regenerate the PNG exports, run `python3 assets/render-icons.py` from the
project root. The renderer uses Ubuntu packages `python3-cairo`, `python3-gi`,
and `gir1.2-rsvg-2.0`. Generated PNGs are not tracked in Git.

The `tray/` directory contains separate 24-pixel symbolic artwork for the top
bar. A simple numpad, an integrated lower-right USB or Bluetooth symbol, and a
thin battery bar occupy one square icon. Every asset uses the same symbolic
ink, with reduced opacity for the empty battery track, so the desktop can
recolor it. The connection symbol does not imply charging.

Connected assets are named `gmmk-numpad-{usb|bluetooth}-{000..100}-symbolic.svg`
at five-percent intervals. The full percentage remains in the menu and
tooltip. A real zero has a continuous empty track; unavailable or stale
battery information has a dashed track. Separate `offline`, `ambiguous`, and
`unknown` icons have explicit connection badges and a dashed track.

Regenerate the tracked tray SVGs with `python3 assets/render-tray-icons.py`.
Generation uses only Python's standard library. Check reproducibility without
writing with `python3 assets/render-tray-icons.py --check`. Add
`--contact-sheet /tmp/gmmk-tray-icons.png` to render examples at actual size
and enlarged on dark and light backgrounds using the renderer packages above.

The artwork was created for this project and contains no Glorious logo or
wordmark. It is available under the project [MIT License](../LICENSE).

Copyright 2026 OpenGMMK Numpad Editor contributors.
