"""
The AI service on Modal deploys by itself: with a release when its code changed (before the website), and for
staging as its own app on pushes to main, so staging never calls the live service with code it does not know.
"""

from django.conf import settings
from django.test import SimpleTestCase

BASE = settings.BASE_DIR


def read(path):
    return (BASE / path).read_text()


class ModalDeployTests(SimpleTestCase):
    def test_a_release_deploys_the_ai_service_first_when_its_code_changed(self):
        deploy = read(".github/workflows/deploy.yml")
        modal = deploy[deploy.index("  deploy-modal:"):deploy.index("  deploy-web:")]
        self.assertIn("github.event_name == 'release'", modal)
        self.assertIn('git diff --quiet "$PREV" "$GITHUB_SHA" -- beetlesgallery/tools/', modal)
        self.assertIn("if: steps.changes.outputs.deploy == 'true'", modal)
        self.assertIn("pixi run modal deploy beetlesgallery/tools/modal_ibbi_api.py", modal)
        self.assertNotIn("IBBI_MODAL_APP", modal)   # the live app keeps its name (and its address)
        web = deploy[deploy.index("  deploy-web:"):]
        self.assertIn("needs: deploy-modal", web)
        self.assertIn("!failure() && !cancelled()", web)   # runs when the AI deploy had nothing to do, never after it failed

    def test_staging_deploys_its_own_ai_service_and_calls_it(self):
        staging = read(".github/workflows/deploy-staging.yml")
        job = staging[staging.index("  deploy-staging-modal:"):]
        self.assertIn("IBBI_MODAL_APP: ibbi-api-staging", job)
        self.assertIn("-- beetlesgallery/tools/", job)
        self.assertIn("if: vars.STAGING_ENABLED == 'true'", job)
        address = "https://christophermarais--ibbi-api-staging-fastapi-app.modal.run/analyze"
        self.assertIn(address, read("scripts/staging_setup.sh"))
        self.assertIn(address, read("scripts/staging_deploy.sh"))
        self.assertIn("MODAL_API_URL)=", read("scripts/staging_setup.sh"))   # production's address is not copied

    def test_the_app_name_comes_from_the_environment_and_defaults_to_the_live_app(self):
        source = read("beetlesgallery/tools/modal_ibbi_api.py")
        self.assertIn('APP_NAME = os.environ.get("IBBI_MODAL_APP") or "ibbi-api"', source)
        self.assertIn("app = modal.App(APP_NAME)", source)
        self.assertRegex(settings.MODAL_API_URL, r"--ibbi-api-fastapi-app\.modal\.run")
