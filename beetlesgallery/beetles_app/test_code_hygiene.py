"""
Code hygiene (#507): the app logs through `logging` rather than print(), dead files stay gone, and CI lints the code
with ruff. Static checks, like test_ci_migrations_check.
"""
import ast
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

APP_DIR = Path(__file__).resolve().parent


def app_modules():
    """The app's own code: everything but tests, migrations and management commands (those write to stdout)."""
    for path in sorted(APP_DIR.rglob("*.py")):
        rel = path.relative_to(APP_DIR)
        if rel.name.startswith("test_") or rel.parts[0] in ("migrations", "management"):
            continue
        yield rel, path


def print_calls(source):
    """Line numbers of print(...) calls; a print in a comment or a string doesn't count."""
    return [node.lineno for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print"]


class NoPrintTests(SimpleTestCase):
    def test_the_finder_spots_a_print_call_and_nothing_else(self):
        self.assertEqual(print_calls('x = 1\nprint("hi")\n# print("no")\ns = "print(1)"\n'), [2])

    def test_app_code_logs_instead_of_printing(self):
        modules = list(app_modules())
        self.assertIn(Path("views.py"), [rel for rel, _ in modules])
        found = [f"{rel}:{line}" for rel, path in modules for line in print_calls(path.read_text(encoding="utf-8"))]
        self.assertEqual(found, [], "use logger = logging.getLogger(__name__) instead of print()")


class DeadCodeTests(SimpleTestCase):
    def test_the_unused_header_script_stays_gone(self):
        # base.html never loaded it
        self.assertFalse((settings.BASE_DIR / "beetlesgallery" / "static" / "js" / "header_basehtml.js").exists())

    def test_views_define_the_annotation_page_once(self):
        tree = ast.parse((APP_DIR / "views.py").read_text(encoding="utf-8"))
        names = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
        self.assertEqual(names.count("tool_annotate"), 1)


class LintInCiTests(SimpleTestCase):
    def test_ci_runs_ruff_with_the_repo_rules(self):
        workflow = (settings.BASE_DIR / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        self.assertIn("astral-sh/ruff-action@", workflow)
        rules = (settings.BASE_DIR / "ruff.toml").read_text(encoding="utf-8")
        self.assertIn('"F"', rules)
        self.assertIn("migrations", rules)
