"""Small, synchronous device operations, called off the GTK main thread."""
from __future__ import annotations

import time

from device import scan_devices, select_device
from protocol import build_status_request, parse_status, build_keymap, build_select_profile
from transport import StockTransport


def connected_device():
    device = select_device(scan_devices())
    if device is None:
        raise RuntimeError("Connect exactly one supported numpad using its USB cable, then refresh.")
    return device


def read_status():
    with StockTransport(connected_device()) as transport:
        transport.send_feature(build_status_request())
        time.sleep(0.03)
        result = parse_status(transport.get_feature())
        time.sleep(0.03)
        return result


def write_bindings(profile, slot, layer):
    # Encode everything before opening the device: invalid drafts send no data.
    frames = [(build_keymap(profile, slot, layer), 0.15),
              (build_select_profile(slot, layer), 0.05)]
    with StockTransport(connected_device()) as transport:
        for report, delay in frames:
            transport.send_feature(report)
            time.sleep(delay)


def select_profile(slot, layer):
    report = build_select_profile(slot, layer)
    with StockTransport(connected_device()) as transport:
        transport.send_feature(report)
        time.sleep(0.05)


def write_lighting(profile, slot, layer, settings):
    from protocol import build_rgb_effect, build_rgb_settings, build_per_key_rgb
    rgb = profile["rgb"]
    frames = [(build_rgb_effect(rgb, slot, layer), 0.1),
              (build_rgb_settings(rgb, slot, layer, **settings), 0.1),
              (build_per_key_rgb(rgb, slot, layer), 0.1)]
    frames.append((build_select_profile(slot, layer), 0.05))
    with StockTransport(connected_device()) as transport:
        for report, delay in frames:
            transport.send_feature(report)
            time.sleep(delay)
