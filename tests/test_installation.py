"""Installation/removal tests use explicit temporary homes, never host settings."""
import hashlib
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import installation


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.home = self.root / "home with spaces"
        self.source.mkdir()
        self.home.mkdir()
        for name, data in {
            "run.sh": "#!/bin/sh\nexit 0\n", "installation.py": "# installer fixture\n",
            "install.sh": "# install fixture\n", "uninstall.sh": "# uninstall fixture\n",
            "app.py": "# original application\n", "README.md": "guide\n",
            "docs/guide.md": "instructions\n", "examples/default.json": '{"example":true}\n',
            f"assets/{installation.APP_ID}.svg": '<svg xmlns="http://www.w3.org/2000/svg"/>\n',
            f"assets/icons/48x48/{installation.APP_ID}.png": "PNG fixture\n",
        }.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data)
        self.application = self.home / installation.APPLICATION_RELATIVE
        self.launcher = self.home / installation.LAUNCHER_RELATIVE

    def install(self):
        return installation.install_app(self.source, self.home)

    def manifest(self):
        return json.loads((self.application / installation.MANIFEST_NAME).read_text())

    def make_legacy_installation(self, custom_launcher=False):
        """Recreate the original installed manifest without running old code."""
        self.install()
        data = self.manifest()
        for relative in data["desktop_files"]:
            (self.home / relative).unlink()
        legacy = self.home / installation.LEGACY_LAUNCHER_RELATIVE
        original = b"[Desktop Entry]\nName=GMMK Numpad Editor\n"
        legacy.write_bytes(b"# personal launcher\n" if custom_launcher else original)
        (self.application / installation.MANIFEST_NAME).write_text(json.dumps({
            "schema_version": 1, "files": data["files"],
            "launcher_sha256": hashlib.sha256(original).hexdigest(),
        }))
        return legacy

    def test_tracks_every_installed_asset_hash_and_executable_script(self):
        result = self.install()
        manifest = self.manifest()
        self.assertEqual(set(manifest["files"]), set(result["installed"]))
        self.assertIn("docs/guide.md", manifest["files"])
        self.assertIn("examples/default.json", manifest["files"])
        for name, digest in manifest["files"].items():
            self.assertEqual(hashlib.sha256((self.application / name).read_bytes()).hexdigest(), digest)
        self.assertEqual(stat.S_IMODE((self.application / "run.sh").stat().st_mode), 0o755)
        self.assertIn(f'Exec="{self.application}/run.sh"', self.launcher.read_text())
        self.assertIn("Name=OpenGMMK Numpad Editor\n", self.launcher.read_text())
        self.assertIn(f"Icon={installation.APP_ID}\n", self.launcher.read_text())
        self.assertIn(f"StartupWMClass={installation.APP_ID}\n", self.launcher.read_text())
        self.assertEqual(self.launcher.name, installation.APP_ID + ".desktop")
        for relative, digest in manifest["desktop_files"].items():
            self.assertEqual(hashlib.sha256((self.home / relative).read_bytes()).hexdigest(), digest)

    def test_migrates_original_manifest_launcher_and_installs_icons(self):
        legacy = self.make_legacy_installation()
        result = self.install()
        self.assertEqual(result["preserved"], [])
        self.assertFalse(legacy.exists())
        self.assertTrue(self.launcher.exists())
        manifest = self.manifest()
        self.assertEqual(manifest["schema_version"], 2)
        self.assertNotIn(installation.LEGACY_LAUNCHER_RELATIVE, manifest["desktop_files"])
        self.assertIn(installation.LAUNCHER_RELATIVE, manifest["desktop_files"])
        self.assertEqual(len(manifest["desktop_files"]), 3)
        for source, relative in installation.ICON_ASSETS.items():
            if (self.source / source).exists():
                self.assertEqual((self.source / source).read_bytes(), (self.home / relative).read_bytes())

    def test_migration_preserves_customized_original_launcher(self):
        legacy = self.make_legacy_installation(custom_launcher=True)
        result = self.install()
        self.assertIn(installation.LEGACY_LAUNCHER_RELATIVE, result["preserved"])
        self.assertEqual(legacy.read_text(), "# personal launcher\n")
        self.assertTrue(self.launcher.exists())
        installation.uninstall_app(self.home)
        self.assertEqual(legacy.read_text(), "# personal launcher\n")

    def test_unrecognized_canonical_launcher_does_not_remove_original_launcher(self):
        legacy = self.make_legacy_installation()
        self.launcher.write_text("# independent canonical launcher\n")
        result = self.install()
        self.assertIn(installation.LAUNCHER_RELATIVE, result["preserved"])
        self.assertEqual(self.launcher.read_text(), "# independent canonical launcher\n")
        self.assertTrue(legacy.exists())

    def test_old_manifest_can_still_be_uninstalled(self):
        legacy = self.make_legacy_installation()
        result = installation.uninstall_app(self.home)
        self.assertTrue(result["manifest_found"])
        self.assertFalse(legacy.exists())
        self.assertFalse(self.application.exists())

    def test_modified_or_preexisting_icon_is_never_overwritten_or_removed(self):
        svg_relative = installation.ICON_ASSETS[f"assets/{installation.APP_ID}.svg"]
        svg = self.home / svg_relative
        svg.parent.mkdir(parents=True)
        svg.write_text("personal icon")
        result = self.install()
        self.assertIn(svg_relative, result["preserved"])
        self.assertNotIn(svg_relative, self.manifest()["desktop_files"])
        installation.uninstall_app(self.home)
        self.assertEqual(svg.read_text(), "personal icon")
        svg.unlink()
        self.install()
        svg.write_text("edited installed icon")
        result = self.install()
        self.assertIn(svg_relative, result["preserved"])
        result = installation.uninstall_app(self.home)
        self.assertIn(svg_relative, result["preserved"])
        self.assertEqual(svg.read_text(), "edited installed icon")

    def test_manifest_cannot_adopt_other_desktop_or_home_files(self):
        self.install()
        manifest = self.manifest()
        for unsafe in (".ssh/config", "../outside", ".local/share/applications/other.desktop"):
            with self.subTest(unsafe=unsafe):
                manifest["desktop_files"][unsafe] = "0" * 64
                (self.application / installation.MANIFEST_NAME).write_text(json.dumps(manifest))
                with self.assertRaises(installation.InstallationError):
                    installation.uninstall_app(self.home)
                self.assertTrue(self.launcher.exists())
                self.assertTrue((self.application / "app.py").exists())
                del manifest["desktop_files"][unsafe]

    def test_clean_uninstall_removes_managed_assets_manifest_and_launcher(self):
        self.install()
        result = installation.uninstall_app(self.home)
        self.assertTrue(result["manifest_found"])
        self.assertFalse(self.application.exists())
        self.assertFalse(self.launcher.exists())
        self.assertTrue((self.source / "app.py").exists())

    def test_preserves_unknown_profile_json_modified_managed_file_and_external_data(self):
        self.install()
        (self.application / "my-profile.json").write_text('{"document_kind":"local-draft"}')
        (self.application / "app.py").write_text("# user modified application\n")
        outside = self.home / "Documents" / "profiles.json"
        outside.parent.mkdir()
        outside.write_text("external saved profiles")
        result = installation.uninstall_app(self.home)
        self.assertEqual((self.application / "app.py").read_text(), "# user modified application\n")
        self.assertTrue((self.application / "my-profile.json").exists())
        self.assertEqual(outside.read_text(), "external saved profiles")
        self.assertEqual(result["preserved"], ["app.py", "my-profile.json"])
        self.assertFalse((self.application / installation.MANIFEST_NAME).exists())

    def test_modified_managed_example_json_is_preserved(self):
        self.install()
        example = self.application / "examples/default.json"
        example.write_text('{"my_settings":true}')
        installation.uninstall_app(self.home)
        self.assertEqual(example.read_text(), '{"my_settings":true}')

    def test_self_reinstall_never_adopts_unknown_profile(self):
        self.install()
        local = self.application / "my-profile.json"
        local.write_text("my saved settings")
        installation.install_app(self.application, self.home)
        self.assertNotIn("my-profile.json", self.manifest()["files"])
        installation.uninstall_app(self.home)
        self.assertEqual(local.read_text(), "my saved settings")

    def test_reinstall_keeps_modified_files_and_unrecognized_collisions(self):
        self.install()
        (self.application / "app.py").write_text("# custom app\n")
        unknown = self.application / "future.py"
        unknown.write_text("# personal file\n")
        (self.source / "future.py").write_text("# new package file\n")
        result = self.install()
        self.assertIn("app.py", result["preserved"])
        self.assertIn("future.py", result["preserved"])
        self.assertNotIn("future.py", self.manifest()["files"])
        installation.uninstall_app(self.home)
        self.assertEqual(unknown.read_text(), "# personal file\n")
        self.assertEqual((self.application / "app.py").read_text(), "# custom app\n")

    def test_modified_launcher_is_preserved(self):
        self.install()
        self.launcher.write_text("# custom launcher\n")
        installation.uninstall_app(self.home)
        self.assertEqual(self.launcher.read_text(), "# custom launcher\n")

    def test_missing_manifest_never_deletes_existing_files(self):
        self.application.mkdir(parents=True)
        saved = self.application / "saved.json"
        saved.write_text("keep")
        result = installation.uninstall_app(self.home)
        self.assertFalse(result["manifest_found"])
        self.assertEqual(saved.read_text(), "keep")

    def test_path_traversal_manifest_is_rejected_before_deleting_any_file(self):
        self.install()
        manifest = self.manifest()
        for unsafe in ("../outside.txt", "/tmp/outside.txt", "a/../../outside", "a//b", "a\\b", installation.MANIFEST_NAME):
            with self.subTest(unsafe=unsafe):
                manifest["files"] = {"app.py": hashlib.sha256((self.application / "app.py").read_bytes()).hexdigest(), unsafe: "0" * 64}
                (self.application / installation.MANIFEST_NAME).write_text(json.dumps(manifest))
                with self.assertRaises(installation.InstallationError):
                    installation.uninstall_app(self.home)
                self.assertTrue((self.application / "app.py").exists())

    def test_managed_symlink_is_rejected_without_touching_target_or_other_files(self):
        self.install()
        outside = self.root / "outside.txt"
        outside.write_text("keep external target")
        path = self.application / "app.py"
        path.unlink()
        path.symlink_to(outside)
        with self.assertRaises(installation.InstallationError):
            installation.uninstall_app(self.home)
        self.assertEqual(outside.read_text(), "keep external target")
        self.assertTrue((self.application / "run.sh").exists())

    def test_source_and_destination_directory_symlinks_are_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.source / "linked-directory").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(installation.InstallationError):
            self.install()
        self.assertFalse(self.application.exists())
        (self.source / "linked-directory").unlink()
        self.application.parent.mkdir(parents=True)
        self.application.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(installation.InstallationError):
            self.install()
        with self.assertRaises(installation.InstallationError):
            installation.uninstall_app(self.home)

    def test_unknown_symlink_is_preserved_without_following_it(self):
        self.install()
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "saved.json").write_text("keep")
        (self.application / "my-link").symlink_to(outside, target_is_directory=True)
        result = installation.uninstall_app(self.home)
        self.assertIn("my-link", result["preserved"])
        self.assertEqual((outside / "saved.json").read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
