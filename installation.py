"""Per-user managed installation; explicit home arguments keep tests isolated."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

APPLICATION_RELATIVE = ".local/share/gmmk-numpad-configurator"
APP_ID = "io.github.opengmmk.NumpadEditor"
APP_NAME = "OpenGMMK Numpad Editor"
LAUNCHER_RELATIVE = f".local/share/applications/{APP_ID}.desktop"
LEGACY_LAUNCHER_RELATIVE = ".local/share/applications/gmmk-numpad-editor.desktop"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
ICON_ASSETS = {f"assets/{APP_ID}.svg": f".local/share/icons/hicolor/scalable/apps/{APP_ID}.svg"}
ICON_ASSETS.update({f"assets/icons/{size}x{size}/{APP_ID}.png":
                    f".local/share/icons/hicolor/{size}x{size}/apps/{APP_ID}.png"
                    for size in ICON_SIZES})
_DESKTOP_PATHS = frozenset((LAUNCHER_RELATIVE, LEGACY_LAUNCHER_RELATIVE, *ICON_ASSETS.values()))
MANIFEST_NAME = ".managed-files.json"
_HASH = re.compile(r"[0-9a-f]{64}\Z")


class InstallationError(ValueError):
    pass


def _check_path(path: Path, directory: bool = False) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise InstallationError(f"Refusing a symlink in an installation path: {current}")
    if path.exists():
        mode = path.lstat().st_mode
        if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
            raise InstallationError(f"Unexpected installation path type: {path}")


def _relative(value: str) -> str:
    if (not isinstance(value, str) or not value or "\\" in value
            or any(part in ("", ".", "..") for part in value.split("/"))
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or value == MANIFEST_NAME):
        raise InstallationError(f"Unsafe managed file path: {value!r}")
    return value


def _file(root: Path, relative: str) -> Path:
    path = root.joinpath(*_relative(relative).split("/"))
    _check_path(path)
    return path


def _digest(path: Path) -> str:
    _check_path(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InstallationError(f"Duplicate manifest field: {key}")
        result[key] = value
    return result


def _manifest(application: Path):
    path = application / MANIFEST_NAME
    _check_path(path)
    if not path.exists():
        return None
    try:
        with path.open("rb") as stream:
            data = stream.read(4 * 1024 * 1024 + 1)
        if len(data) > 4 * 1024 * 1024:
            raise InstallationError("Installation manifest exceeds 4 MiB")
        result = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise InstallationError("Invalid installation manifest") from error
    if not isinstance(result, dict) or type(result.get("schema_version")) is not int:
        raise InstallationError("Unsupported installation manifest structure")
    if result["schema_version"] == 1 and set(result) == {"schema_version", "files", "launcher_sha256"}:
        expected = result["launcher_sha256"]
        if expected is not None and (not isinstance(expected, str) or _HASH.fullmatch(expected) is None):
            raise InstallationError("Invalid launcher SHA-256")
        # The v1 launcher always used this exact legacy filename.
        result = {"schema_version": 2, "files": result["files"],
                  "desktop_files": {} if expected is None else {LEGACY_LAUNCHER_RELATIVE: expected}}
    elif result["schema_version"] != 2 or set(result) != {"schema_version", "files", "desktop_files"}:
        raise InstallationError("Unsupported installation manifest structure")
    if not isinstance(result["files"], dict) or not isinstance(result["desktop_files"], dict):
        raise InstallationError("Unsupported installation manifest structure")
    for relative, expected in result["files"].items():
        _file(application, relative)
        if not isinstance(expected, str) or _HASH.fullmatch(expected) is None:
            raise InstallationError("Invalid managed-file SHA-256")
    for relative, expected in result["desktop_files"].items():
        if relative not in _DESKTOP_PATHS:
            raise InstallationError("Unexpected managed desktop file path")
        if not isinstance(expected, str) or _HASH.fullmatch(expected) is None:
            raise InstallationError("Invalid desktop-file SHA-256")
    return result


def _write(path: Path, data: bytes, mode=0o644) -> None:
    _check_path(path)
    _check_path(path.parent, directory=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
            temporary = stream.name
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        _check_path(path)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            os.unlink(temporary)


def _locations(home):
    home = Path(os.path.abspath(Path.home() if home is None else Path(home)))
    if any(ord(c) < 32 or ord(c) == 127 for c in str(home)):
        raise InstallationError("Home path contains control characters")
    _check_path(home, directory=True)
    application, launcher = home / APPLICATION_RELATIVE, home / LAUNCHER_RELATIVE
    _check_path(application, directory=True)
    _check_path(launcher)
    for relative in _DESKTOP_PATHS:
        _check_path(home / relative)
    return application, launcher


def _source_files(source):
    _check_path(source, directory=True)
    if not source.is_dir():
        raise InstallationError("Application source folder does not exist")
    result = []
    for current, directories, files in os.walk(source, followlinks=False):
        directories[:] = sorted(d for d in directories if d not in (".git", ".codex", ".agents", "__pycache__") and not d.startswith("test-results"))
        for directory in directories:
            _check_path(Path(current) / directory, directory=True)
        for name in files:
            if name == MANIFEST_NAME or name.endswith((".pyc", ".pyo")) or name.startswith("test-results"):
                continue
            path = Path(current) / name
            _check_path(path)
            result.append(_relative(path.relative_to(source).as_posix()))
    return sorted(result)


def _launcher(application):
    quoted = str(application / "run.sh").replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$").replace("%", "%%")
    return (f"[Desktop Entry]\nType=Application\nName={APP_NAME}\n"
            "Comment=Edit stock Glorious GMMK Numpad profiles and lighting over USB\n"
            f'Exec="{quoted}"\nIcon={APP_ID}\nTerminal=false\n'
            f"StartupWMClass={APP_ID}\n"
            "Categories=Settings;HardwareSettings;\nStartupNotify=true\n"
            "Keywords=Glorious;GMMK;Numpad;RGB;Keyboard;\n").encode("utf-8")


def install_app(source: Path, home: Path | None = None) -> dict:
    source = Path(os.path.abspath(source))
    application, launcher = _locations(home)
    previous = _manifest(application)
    managed = {} if previous is None else dict(previous["files"])
    desktop_files = {} if previous is None else dict(previous["desktop_files"])
    home = application.parents[2]
    if source == application:
        if previous is None:
            raise InstallationError("Cannot adopt an application folder without its managed-file manifest")
        files = sorted(name for name in managed if (application / name).exists())
    else:
        files = _source_files(source)
    if "run.sh" not in files or "installation.py" not in files:
        raise InstallationError("Application source must include run.sh and installation.py")
    # Validate every source/destination path before the first write.
    for name in files:
        _file(source, name)
        _file(application, name)
    preserved, installed = [], []
    for name in files:
        origin, destination = _file(source, name), _file(application, name)
        if destination.exists() and (name not in managed or _digest(destination) != managed[name]):
            preserved.append(name)
            continue
        data = origin.read_bytes()
        if source != application:
            _write(destination, data, 0o755 if name.endswith(".sh") else 0o644)
            installed.append(name)
        managed[name] = hashlib.sha256(data).hexdigest()
    launcher_hash = desktop_files.get(LAUNCHER_RELATIVE)
    if not launcher.exists() or (launcher_hash is not None and _digest(launcher) == launcher_hash):
        data = _launcher(application)
        _write(launcher, data)
        desktop_files[LAUNCHER_RELATIVE] = hashlib.sha256(data).hexdigest()
        legacy = home / LEGACY_LAUNCHER_RELATIVE
        if legacy.exists():
            expected = desktop_files.get(LEGACY_LAUNCHER_RELATIVE)
            if expected is not None and _digest(legacy) == expected:
                legacy.unlink()
                desktop_files.pop(LEGACY_LAUNCHER_RELATIVE)
            else:
                preserved.append(LEGACY_LAUNCHER_RELATIVE)
    else:
        preserved.append(LAUNCHER_RELATIVE)
    for asset, relative in ICON_ASSETS.items():
        origin, destination = _file(application, asset), home / relative
        if not origin.exists():
            continue
        expected = desktop_files.get(relative)
        if destination.exists() and (expected is None or _digest(destination) != expected):
            preserved.append(relative)
            continue
        data = origin.read_bytes()
        _write(destination, data)
        desktop_files[relative] = hashlib.sha256(data).hexdigest()
    manifest = {"schema_version": 2, "files": managed, "desktop_files": desktop_files}
    _write(application / MANIFEST_NAME, (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return {"application": str(application), "installed": installed, "preserved": preserved}


def uninstall_app(home: Path | None = None) -> dict:
    application, launcher = _locations(home)
    manifest = _manifest(application)  # Preflight all paths before any deletion.
    if manifest is None:
        return {"removed": [], "preserved": [], "manifest_found": False}
    removed = []
    for name, expected in manifest["files"].items():
        path = _file(application, name)
        if path.exists() and _digest(path) == expected:
            path.unlink()
            removed.append(name)
    desktop_preserved = []
    home = application.parents[2]
    for relative, expected in manifest["desktop_files"].items():
        path = home / relative
        if path.exists():
            if _digest(path) == expected:
                path.unlink()
            else:
                desktop_preserved.append(relative)
    (application / MANIFEST_NAME).unlink()
    # Never recursively delete a tree: only empty directories may disappear.
    preserved = []
    for current, directories, files in os.walk(application, topdown=False, followlinks=False):
        current = Path(current)
        preserved.extend((current / name).relative_to(application).as_posix() for name in files)
        for name in directories:
            path = current / name
            if path.is_symlink():
                preserved.append(path.relative_to(application).as_posix())
        try:
            current.rmdir()
        except OSError:
            pass
    preserved.extend(desktop_preserved)
    return {"removed": removed, "preserved": sorted(preserved), "manifest_found": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument("--source", type=Path, default=Path(__file__).absolute().parent)
    args = parser.parse_args()
    try:
        if args.action == "install":
            result = install_app(args.source)
            print(f"Installed {APP_NAME}. Find it in Ubuntu’s applications menu.")
            print("Application folder: " + result["application"])
            print("For USB access, run install-device-access.sh once in a normal terminal.")
        else:
            result = uninstall_app()
            print("Removed unchanged managed application files." if result["manifest_found"] else "No managed installation manifest found; existing files were preserved.")
            print("Saved profiles and the slider helper were preserved.")
            print("Optional USB access removal: sudo rm /etc/udev/rules.d/69-gmmk-numpad-configurator.rules")
            print("Then run sudo udevadm control --reload-rules and reconnect the numpad.")
        if result["preserved"]:
            print("Preserved modified or unrecognized files: " + ", ".join(result["preserved"]))
    except (OSError, InstallationError) as error:
        parser.exit(1, f"Installation action was not completed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
