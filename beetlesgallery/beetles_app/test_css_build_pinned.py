"""
The deploys rebuild style.css with Tailwind 4 (pixi task build-css, its own CLI @tailwindcss/cli). A Dependabot update
once moved Tailwind to a major version the stylesheet wasn't written for, and both deploys stopped at the CSS step.
These checks keep the build on Tailwind 4 and its CLI, and the next major for a deliberate change of its own.
"""
import json
import tomllib
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]
CLI = "node node_modules/@tailwindcss/cli/dist/index.mjs"


class TailwindPinnedTests(SimpleTestCase):
    def test_package_json_pins_tailwind_4_and_its_cli(self):
        deps = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["dependencies"]
        for name in ("tailwindcss", "@tailwindcss/cli"):
            with self.subTest(package=name):
                self.assertRegex(deps[name], r"^[~^]?4\.")

    def test_lockfile_installs_tailwind_4(self):
        packages = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))["packages"]
        for name in ("tailwindcss", "@tailwindcss/cli"):
            with self.subTest(package=name):
                self.assertTrue(packages[f"node_modules/{name}"]["version"].startswith("4."))

    def test_css_tasks_use_the_tailwind_4_cli(self):
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
        self.assertIn("dependency-name: \"@tailwindcss/*\"", config)
        self.assertIn("version-update:semver-major", config)
