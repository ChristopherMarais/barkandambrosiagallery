"""
CI fails a pull request that changes a model without its migration (#282): tests.yml runs
``makemigrations --check --dry-run`` before the tests. A static check, like test_deploy_safety.
"""
from django.conf import settings
from django.test import SimpleTestCase


class MigrationsCheckTests(SimpleTestCase):
    def test_ci_checks_for_missing_migrations_before_the_tests(self):
        workflow = (settings.BASE_DIR / ".github" / "workflows" / "tests.yml").read_text()
        check = workflow.find("manage.py makemigrations --check --dry-run")
        self.assertNotEqual(check, -1, "tests.yml no longer checks for missing migrations")
        self.assertLess(check, workflow.find("manage.py test"), "the check should run before the tests")
