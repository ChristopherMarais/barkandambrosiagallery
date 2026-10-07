"""
Lighthouse fixes (issue #612, the live run's SEO and accessibility notes):
  - every public page has a short meta description, set through the block in base.html
  - small text on the public pages uses the darker grey (text-gray-600), not the faint
    text-gray-400 or text-text/50 that fail WCAG AA contrast on white
  - the Lighthouse CI budgets for contrast and meta descriptions are warnings, so a run
    finishes cleanly and reports the numbers
"""

import json
import re
from pathlib import Path

from django.urls import reverse

from beetlesgallery.beetles_app.testing import PageBehaviourCase

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = REPO / "beetlesgallery" / "templates"

PUBLIC_PAGES = {
    "landing": "/",
    "gallery": reverse("beetles_image_browser"),
    "interactions": reverse("interactions_preview"),
    "sign in": reverse("login"),
    "sign up": reverse("request_access"),
    "password reset": reverse("password_reset"),
    "password reset sent": reverse("password_reset_done"),
}

META_RE = re.compile(r'<meta name="description" content="([^"]*)">')
CLASS_RE = re.compile(r'class="([^"]*)"')
SMALL_TEXT = ("text-xs", "text-sm", "md:text-sm", "text-[10px]", "text-[11px]", "text-[16px]")
FAINT = ("text-gray-400", "text-text/50", "text-text/40")

# Templates whose small text was flagged by the live Lighthouse run (or sits in the shared header/footer)
CHECKED_TEMPLATES = [
    "landing.html",
    "beetles/image_browser.html",
    "beetles/includes/gallery_results.html",
    "beetles/interactions_preview.html",
    "base.html",
]


class LighthouseFixesTests(PageBehaviourCase):
    def test_every_public_page_has_a_short_meta_description(self):
        descriptions = {}
        for name, url in PUBLIC_PAGES.items():
            with self.subTest(page=name):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                match = META_RE.search(response.content.decode())
                self.assertIsNotNone(match, f"{name} has no meta description")
                text = match.group(1).strip()
                self.assertTrue(text, f"{name} has an empty meta description")
                self.assertLessEqual(len(text), 160, f"{name} description is longer than a search snippet")
                self.assertNotIn("<", text)
                descriptions[name] = text
        self.assertEqual(len(set(descriptions.values())), len(descriptions), "descriptions should differ per page")

    def test_taxonomy_browser_has_a_description_for_signed_in_users(self):
        self.client.force_login(self.user)
        html = self.client.get(reverse("taxonomy_browser")).content.decode()
        self.assertIn("taxonomy", META_RE.search(html).group(1))

    def test_landing_page_uses_its_own_description(self):
        html = self.client.get("/").content.decode()
        self.assertIn("identification tools", META_RE.search(html).group(1))

    def test_small_text_does_not_use_the_faint_greys(self):
        for name in CHECKED_TEMPLATES:
            source = (TEMPLATES / name).read_text(encoding="utf-8")
            for lineno, line in enumerate(source.splitlines(), start=1):
                for classes in CLASS_RE.findall(line):
                    tokens = classes.split()
                    if not any(t in SMALL_TEXT for t in tokens):
                        continue
                    faint = [t for t in tokens if t in FAINT]
                    with self.subTest(template=name, line=lineno):
                        self.assertFalse(faint, f"small text with {faint}: {classes}")

    def test_gallery_labels_use_gray_600(self):
        source = (TEMPLATES / "beetles/includes/gallery_results.html").read_text(encoding="utf-8")
        self.assertNotIn("text-text/50", source)
        self.assertIn('<span class="font-semibold text-gray-600 shrink-0">Record ID:</span>', source)

    def test_landing_labels_use_gray_600(self):
        source = (TEMPLATES / "landing.html").read_text(encoding="utf-8")
        self.assertNotIn("text-gray-400", source)
        self.assertIn('uppercase tracking-widest mt-1">Images</span>', source)

    def setUp(self):
        super().setUp()
        self.config = json.loads((REPO / "lighthouserc.json").read_text(encoding="utf-8"))
        self.assertions = self.config["ci"]["assert"]["assertions"]

    def test_every_budget_is_a_warning_so_the_run_completes(self):
        self.assertTrue(self.assertions)
        for name, rule in self.assertions.items():
            with self.subTest(assertion=name):
                self.assertEqual(rule[0], "warn")

    def test_contrast_and_meta_description_budgets_are_set(self):
        self.assertEqual(self.assertions["color-contrast"], ["warn", {"minScore": 1}])
        self.assertEqual(self.assertions["meta-description"], ["warn", {"minScore": 1}])

    def test_lighthouse_urls_cover_the_public_pages(self):
        urls = self.config["ci"]["collect"]["url"]
        for path in ["/", "/beetles/", "/interactions/", "/accounts/login/", "/accounts/signup/"]:
            with self.subTest(path=path):
                self.assertTrue(any(u.endswith(path) for u in urls))
