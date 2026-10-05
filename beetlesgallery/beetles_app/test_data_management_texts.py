"""Data Management explains itself: what each action is for, what the two species tables are, and every column."""
from django.urls import reverse

from beetlesgallery.beetles_app.test_pages import PageTestCase


class DataManagementTextsTests(PageTestCase):
    def page(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("data_management")).content.decode()

    def test_the_species_tables_have_plain_names_and_say_what_they_are_for(self):
        html = self.page(self.superuser)
        self.assertIn(">Accepted species</option>", html)
        self.assertIn(">Synonyms and old names</option>", html)
        self.assertNotIn("Valid Species", html)
        self.assertNotIn("Described Names", html)
        self.assertIn("ground truth", html)
        self.assertIn("turn the name on a vial label", html)

    def test_the_upload_and_update_dialogs_explain_how_to_fill_them(self):
        html = self.page(self.superuser)
        self.assertIn("one row per beetle", html)
        self.assertIn("Start from a download", html)
        self.assertIn("a blank cell clears that field", html)
        self.assertIn('data-testid="data-actions-intro"', html)

    def test_every_column_of_the_templates_is_defined(self):
        html = self.page(self.superuser)
        from django.conf import settings
        downloads = settings.BASE_DIR / "beetlesgallery" / "static" / "downloads"
        columns = set()
        for name in ("beetles_upload_template.csv", "beetles_update_template.csv"):
            columns |= set((downloads / name).read_text(encoding="utf-8-sig").splitlines()[0].split(","))
        for column in sorted(columns | {"label_source", "label_source_detail"}):
            with self.subTest(column=column):
                self.assertIn(f'text-gray-700">{column}</td>', html)
