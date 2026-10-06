"""#570: collectstatic (Docker build, deploys) works with the Tailwind 4 source in static/css."""
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings


class CollectstaticTests(SimpleTestCase):
    def test_collectstatic_skips_the_tailwind_source(self):
        # input.css starts with @import "tailwindcss" (a package, not a file): the manifest storage could not resolve it
        with tempfile.TemporaryDirectory() as root, override_settings(STATIC_ROOT=root):
            call_command("collectstatic", interactive=False, verbosity=0)
            collected = {p.relative_to(root).as_posix() for p in Path(root).rglob("*") if p.is_file()}
        self.assertIn("css/style.css", collected)
        self.assertFalse([name for name in collected if name.startswith("css/input.")])
