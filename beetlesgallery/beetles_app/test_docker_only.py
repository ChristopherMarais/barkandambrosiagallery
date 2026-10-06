"""The site runs only in Docker (Linux) with pixi: no Windows storage class, Linux-only pixi platforms, Docker docs (#584)."""
import tomllib
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage, storages
from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]


class DockerOnlyTests(SimpleTestCase):
    def test_media_uses_djangos_own_file_storage(self):
        self.assertEqual(settings.STORAGES["default"]["BACKEND"], "django.core.files.storage.FileSystemStorage")
        self.assertIs(type(storages["default"]), FileSystemStorage)
        self.assertFalse((ROOT / "beetlesgallery" / "custom_storage.py").exists())

    def test_pixi_resolves_for_linux_only(self):
        manifest = tomllib.loads((ROOT / "pixi.toml").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest["workspace"]["platforms"]), {"linux-64", "linux-aarch64"})
        self.assertFalse(manifest.get("target"), "per-platform dependencies are not needed with one OS")
        self.assertIn("gunicorn", manifest["dependencies"])   # the production server, on both architectures
        self.assertNotIn("pytailwindcss", manifest.get("pypi-dependencies", {}))   # CSS builds with node's tailwindcss
        lock = (ROOT / "pixi.lock").read_text(encoding="utf-8")
        for platform in ("win-64", "osx-64", "osx-arm64"):
            self.assertNotIn(f"{platform}:", lock)

    def test_no_windows_workarounds_in_the_code(self):
        for name in ("image_pipeline.py", "management/commands/validate_uploads.py"):
            text = (ROOT / "beetlesgallery" / "beetles_app" / name).read_text(encoding="utf-8")
            self.assertNotIn("Windows", text, name)
        self.assertNotIn("Windows", (ROOT / "beetlesgallery" / "settings.py").read_text(encoding="utf-8"))

    def test_docs_run_the_site_with_docker_compose(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        guide = (ROOT / "docs" / "wiki" / "Getting-Started.md").read_text(encoding="utf-8")
        for text in (readme, guide):
            self.assertIn("docker compose up", text)
            self.assertIn("Docker Desktop", text)
        self.assertNotIn("outside Docker", (ROOT / "docs" / "email_setup.md").read_text(encoding="utf-8"))
        self.assertNotIn("run `pixi install` locally", guide)
