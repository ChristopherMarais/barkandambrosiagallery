"""
The deploys rebuild style.css with Tailwind 3 (pixi task build-css). A Dependabot security update once moved
tailwindcss to 4, which can't build this stylesheet, and both deploys stopped at the CSS step. These checks keep the
build pinned to Tailwind 3 and calling its own CLI.
"""
import json
import tomllib
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]
CLI = "node node_modules/tailwindcss/lib/cli.js"


class TailwindPinnedTests(SimpleTestCase):
    def test_package_json_pins_tailwind_3(self):
        version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["dependencies"]["tailwindcss"]
        self.assertRegex(version, r"^[~^]?3\.")

    def test_lockfile_installs_tailwind_3(self):
        lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
        self.assertTrue(lock["packages"]["node_modules/tailwindcss"]["version"].startswith("3."))

    def test_css_tasks_use_the_tailwind_3_cli(self):
        tasks = tomllib.loads((ROOT / "pixi.toml").read_text(encoding="utf-8"))["tasks"]
        for name in ("build-css", "watch-css"):
            with self.subTest(task=name):
                self.assertTrue(str(tasks[name]).startswith(CLI))

    def test_ci_builds_the_css(self):
        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        self.assertIn("pixi run build-css", workflow)

    def test_dependabot_skips_major_tailwind(self):
        config = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        self.assertIn("dependency-name: tailwindcss", config)
        self.assertIn("version-update:semver-major", config)
