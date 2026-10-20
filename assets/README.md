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

The artwork was created for this project and contains no Glorious logo or
wordmark. It is available under the project [MIT License](../LICENSE).

Copyright 2026 OpenGMMK Numpad Editor contributors.
