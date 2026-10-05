"""Data Management's field definitions name the identification tiers, not the old label sources (#390 follow-up)."""
import re

from django.urls import reverse

from beetlesgallery.beetles_app.testing import PageBehaviourCase


class TierNamesTextTests(PageBehaviourCase):
    def test_label_source_lists_the_tiers(self):
        self.client.force_login(self.staff)
        page = self.client.get(reverse("data_management")).content.decode()
        cell = re.search(r'data-testid="label-source-help">(.*?)</td>', page, re.S).group(1)
        for tier in ("Taxonomist ID", "Expert ID", "Community ID", "External ID", "No ID"):
            self.assertIn(tier, cell)
        self.assertIn("still work", cell)   # the old names are aliases only
