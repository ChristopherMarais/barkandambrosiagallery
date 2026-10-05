"""pixi.lock is format v7; the Docker image and every workflow use one pinned pixi that reads it (no upgrade warning)."""
import re
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]


class PixiVersionTests(SimpleTestCase):
    def test_the_lock_file_is_format_7(self):
        self.assertEqual((ROOT / "pixi.lock").read_text().splitlines()[0], "version: 7")

    def test_docker_and_workflows_pin_the_same_pixi(self):
        docker = re.findall(r"ENV PIXI_VERSION=(v[\d.]+)", (ROOT / "Dockerfile").read_text())
        workflows = [v for f in (ROOT / ".github" / "workflows").glob("*.yml")
                     for v in re.findall(r"pixi-version: (v[\d.]+)", f.read_text())]
        self.assertEqual(len(docker), 1)
        self.assertTrue(workflows)
        self.assertEqual(set(workflows), set(docker))
