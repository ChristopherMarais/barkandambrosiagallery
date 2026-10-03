"""
The game's rank pickers only offer names at their own rank, never overlap, drop
subtribes, and file each genus under the parents most rows give it (game_taxa.py).
"""
from io import StringIO

from django.core.management import call_command
from django.urls import reverse

from beetlesgallery.beetles_app import game_taxa
from beetlesgallery.beetles_app.testing import make_taxon
from beetlesgallery.beetles_app.test_game import GameCase


class MessyTaxaTests(GameCase):
    def setUp(self):
        super().setUp()
        make_taxon(subfamily="Scolytinae", tribe="Phloeosinini", genus="Chramesus", species="chapuisii")
        make_taxon(subfamily="Scolytinae", tribe="Phloeosinini", genus="Chramesus", species="dentatus")
        # A tribe-only record with the tribe name pushed into the genus column.
        make_taxon(subfamily="Scolytinae", tribe="", genus="Phloeosinini", species="")
        # A subtribe filed as the tribe.
        make_taxon(subfamily="Scolytinae", tribe="Xyleborina", subtribe="Xyleborina", genus="Xylosandrus",
                   species="crassiusculus")
        # One stray row puts Xyleborus under another tribe and subfamily.
        make_taxon(subfamily="Platypodinae", tribe="Platypodini", genus="Xyleborus", species="stray")
        # A tribe filed once under the wrong subfamily.
        make_taxon(subfamily="Platypodinae", tribe="Xyleborini", genus="Xyleborus", species="dispar")
        self.client.force_login(self.user)

    def get(self, rank, **parents):
        return self.client.get(reverse("game_taxa"), {"rank": rank, **parents}).json()["options"]

    def values(self, rank, **parents):
        return [o["value"] for o in self.get(rank, **parents)]

    def test_genus_list_has_no_tribes(self):
        self.assertNotIn("Phloeosinini", self.values("genus"))
        self.assertNotIn("Phloeosinini", self.values("genus", subfamily="Scolytinae"))
        self.assertIn("Chramesus", self.values("genus", subfamily="Scolytinae", tribe="Phloeosinini"))

    def test_subtribes_are_not_offered(self):
        self.assertNotIn("Xyleborina", self.values("tribe", subfamily="Scolytinae"))
        # The genus still shows, just without a tribe to fill in.
        [xylosandrus] = [o for o in self.get("genus") if o["value"] == "Xylosandrus"]
        self.assertEqual((xylosandrus["subfamily"], xylosandrus["tribe"]), ("Scolytinae", ""))

    def test_genus_takes_the_majority_parents(self):
        [xyleborus] = [o for o in self.get("genus") if o["value"] == "Xyleborus"]
        self.assertEqual((xyleborus["subfamily"], xyleborus["tribe"]), ("Scolytinae", "Xyleborini"))
        self.assertNotIn("Xyleborus", self.values("genus", subfamily="Platypodinae"))
        self.assertNotIn("Xyleborus", self.values("genus", tribe="Platypodini"))

    def test_tribe_takes_the_majority_subfamily(self):
        self.assertNotIn("Xyleborini", self.values("tribe", subfamily="Platypodinae"))
        self.assertIn("Xyleborini", self.values("tribe", subfamily="Scolytinae"))

    def test_no_name_is_offered_at_two_ranks(self):
        subfamilies = set(self.values("subfamily"))
        tribes = {t for s in subfamilies for t in self.values("tribe", subfamily=s)}
        genera = set(self.values("genus"))
        self.assertFalse(subfamilies & tribes or subfamilies & genera or tribes & genera)

    def test_species_of_a_genus(self):
        self.assertEqual(self.values("species", genus="chramesus"), ["chapuisii", "dentatus"])

    def test_answers_must_use_offered_names(self):
        self.assertTrue(game_taxa.known({"subfamily": "scolytinae", "genus": "Chramesus", "species": "dentatus"}))
        self.assertFalse(game_taxa.known({"genus": "Phloeosinini"}))
        self.assertFalse(game_taxa.known({"tribe": "Xyleborina"}))
        self.assertFalse(game_taxa.known({"genus": "Chramesus", "species": "affinis"}))

    def test_audit_lists_the_problems(self):
        report = game_taxa.audit()
        self.assertIn(("Phloeosinini", ["genus", "tribe"]), report["names_at_several_ranks"])
        self.assertIn("Xyleborina", report["bad_tribes"])
        self.assertIn("Xyleborus", [g for g, _ in report["genera_with_several_parents"]])
        self.assertIn("Xyleborini", [t for t, _ in report["tribes_in_several_subfamilies"]])
        out = StringIO()
        call_command("audit_taxa", stdout=out)
        self.assertIn("Phloeosinini: genus, tribe", out.getvalue())
