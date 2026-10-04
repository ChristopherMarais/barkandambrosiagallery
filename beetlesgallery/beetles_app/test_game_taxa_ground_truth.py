"""
Issue #420: the game's taxa follow the species list's columns as stored, and the ground-truth CSV's placeholder
rows ("Ipini sp. undetermined") are not offered as names. Nothing depends on how a name ends.
"""
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command

from beetlesgallery.beetles_app import game_taxa
from beetlesgallery.beetles_app.test_game import GameCase
from beetlesgallery.beetles_app.testing import make_taxon

# Rows as they appear in valid_species_20261003.csv: (subfamily, tribe, subtribe, genus, species)
GROUND_TRUTH = [
    ("Platypodinae", "Tesserocerini", "Tesserocerina", "Cenocephalus", "pusillus"),
    ("Platypodinae", "Platypodini", "", "Euplatypus", "otiosus"),
    ("Scolytinae", "Ipini", "", "Premnophilus", "maiai"),
    ("Platypodinae", "Tesserocerini", "", "Diapus", "sp. undetermined"),               # genus-level placeholder
    ("Platypodinae", "Tesserocerini", "Tesserocerina", "Tesserocerina", "sp. undetermined"),   # subtribe placeholder
    ("Scolytinae", "Ipini", "", "Ipini", "sp. undetermined"),                          # tribe placeholder
    ("Scolytinae", "", "", "Scolytinae", "sp. undetermined"),                          # subfamily placeholder
]


class GroundTruthTreeTests(GameCase):
    def test_placeholders_are_never_offered_as_names(self):
        tree = game_taxa.build_tree(GROUND_TRUTH)
        self.assertEqual(sorted(tree["genera"]), ["Cenocephalus", "Diapus", "Euplatypus", "Premnophilus"])
        self.assertNotIn("Diapus", tree["species"])   # its only row is a placeholder: no species to offer
        self.assertEqual(tree["tribes"], {"Ipini": "Scolytinae", "Platypodini": "Platypodinae", "Tesserocerini": "Platypodinae"})
        self.assertEqual(tree["genera"]["Cenocephalus"], ("Platypodinae", "Tesserocerini"))
        self.assertEqual(tree["species"]["Premnophilus"], ["maiai"])

    def test_names_need_not_look_like_their_rank(self):
        # a genus that happens to end like a tribe, and a subfamily that doesn't end in -inae, are taken as stored
        rows = [("Subfam0", "Tribe00", "", "Arini", "alpha"), ("Subfam0", "Tribe00", "", "Arini", "beta")]
        tree = game_taxa.build_tree(rows)
        self.assertEqual(tree["subfamilies"], ["Subfam0"])
        self.assertEqual(tree["genera"]["Arini"], ("Subfam0", "Tribe00"))
        self.assertEqual(tree["species"]["Arini"], ["alpha", "beta"])

    def test_the_ground_truth_raises_no_audit_findings(self):
        report = game_taxa.audit(GROUND_TRUTH)
        self.assertEqual(report["names_at_several_ranks"], [])
        self.assertEqual(report["genera_with_several_parents"], [])
        self.assertEqual(report["tribes_in_several_subfamilies"], [])

    def test_audit_compares_the_database_with_the_csv(self):
        make_taxon(valid_species_id="10", subfamily="Platypodinae", tribe="Tesserocerini", genus="Cenocephalus",
                   species="pusillus", scientific_name="Cenocephalus pusillus")
        make_taxon(valid_species_id="99", subfamily="Scolytinae", tribe="Ipini", genus="Ips", species="old",
                   scientific_name="Ips old")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "valid_species.csv"
            path.write_text("valid_species_id,scientificName,subfamily,tribe,subtribe,genus,species,subspecies\n"
                            "10,Cenocephalus pusillus,Platypodinae,Tesserocerini,Tesserocerina,Cenocephalus,pusillus,\n"
                            "100,Euplatypus otiosus,Platypodinae,Platypodini,,Euplatypus,otiosus,\n", encoding="utf-8")
            diff = game_taxa.compare_with_csv(path)
            out = StringIO()
            call_command("audit_taxa", "--csv", str(path), stdout=out)
        self.assertEqual(diff["only_in_csv"], ["100"])
        self.assertIn("99", diff["only_in_database"])
        self.assertEqual(diff["different"], [("10", ["subtribe: '' -> 'Tesserocerina'"])])
        self.assertIn("In the ground-truth CSV but not in the database: 1", out.getvalue())
