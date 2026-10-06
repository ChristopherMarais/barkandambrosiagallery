"""
The annotation page's game filter (#503): brief options that each list something different ("Reported by players"
no longer includes the automatic label check, which is "Disputed validated labels"), and the "most confident first"
sort orders whatever the filter shows instead of switching it to the images with proposals.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from beetlesgallery.beetles_app import game_feedback, game_label_check
from beetlesgallery.beetles_app.models import GameReport, ImageAsset, LabelReview
from beetlesgallery.beetles_app.test_game import AFFINIS, SMALL_TRUST, TrustCase

URL = "/api/v1/beetles/images-with-annotations/"
GENUS_ONLY = dict(AFFINIS, species="")


@override_settings(GAME_PROPOSALS_NEED_LEVEL=False, **SMALL_TRUST)
class ProposalFilterTests(TrustCase):
    def setUp(self):
        super().setUp()
        self.players =[get_user_model().objects.create_user(f"p{i}", password="pw") for i in range(3)]
        self.prove(self.user, self.t_affinis)                     # a proven expert (and three validated images)
        self.waiting = self.roi(validated=False)                  # three players agree on the species
        for p in self.players:
            self.answer(p, self.waiting, check=False, **AFFINIS)
        self.expert = self.roi(validated=False)                   # the expert names it
        self.label(self.user, self.expert, self.t_ferr)
        self.reported_weak = self.roi(validated=False)            # a player flagged it; one player named a genus
        self.answer(self.players[0], self.reported_weak, check=False, **GENUS_ONLY)
        game_feedback.create_report(self.players[1], self.reported_weak, GameReport.Reason.BAD_BOX)
        self.reported = self.roi(self.t_ferr, validated=False)    # a player flagged it; nobody named it
        game_feedback.create_report(self.players[2], self.reported, GameReport.Reason.WRONG_LABEL)
        self.disputed = self.roi(self.t_affinis)                  # the automatic label check flagged its label
        game_label_check.flag([{"roi": self.disputed, "rank": "species", "label": "Xyleborus affinis",
                                "alternative": "Xyleborus ferrugineus", "answers": 5, "wrong_share": 0.8,
                                "agree_share": 1.0}])
        self.applied = self.roi(self.t_affinis, validated=False)  # the game wrote its label
        LabelReview.objects.create(roi=self.applied, decision=LabelReview.Decision.ACCEPTED, taxon=self.t_affinis,
                                   answers=3)
        self.plain = self.roi(validated=False)
        # oldest to newest, in the order made (the clock may not tell them apart)
        self.named = [self.waiting, self.expert, self.reported_weak, self.reported, self.disputed, self.applied,
                      self.plain]
        start = timezone.now() - timedelta(days=1)
        for n, roi in enumerate(self.named):
            ImageAsset.objects.filter(pk=roi.image_asset_id).update(created_at=start + timedelta(minutes=n))
        cache.clear()
        self.client.force_login(self.staff)

    def feed(self, query):
        res = self.client.get(f"{URL}?{query}")
        self.assertEqual(res.status_code, 200, res.content)
        return [r["image_asset_id"] for r in res.json()["results"]]

    def img(self, *rois):
        return [str(roi.image_asset_id) for roi in rois]

    def test_each_filter_lists_its_own_images(self):
        everything = self.feed("")
        self.assertTrue(set(self.img(*self.named)) <= set(everything))
        self.assertEqual(set(self.feed("game=any")), set(self.img(self.waiting, self.expert, self.reported_weak)))
        self.assertEqual(self.feed("game=expert"), self.img(self.expert))
        self.assertEqual(set(self.feed("game=reported")), set(self.img(self.reported_weak, self.reported)))
        self.assertEqual(self.feed("game=disputed"), self.img(self.disputed))
        self.assertEqual(self.feed("game=applied"), self.img(self.applied))

    def test_reported_and_disputed_do_not_overlap(self):
        self.assertTrue(GameReport.objects.filter(roi=self.disputed, status=GameReport.Status.OPEN).exists())
        self.assertNotIn(self.img(self.disputed)[0], self.feed("game=reported"))   # the check is not a player
        self.assertEqual(set(self.feed("game=reported")) & set(self.feed("game=disputed")), set())

    def test_a_player_reporting_a_disputed_label_lists_it_under_both(self):
        game_feedback.create_report(self.players[0], self.disputed, GameReport.Reason.WRONG_LABEL)
        self.assertIn(self.img(self.disputed)[0], self.feed("game=reported"))
        self.assertIn(self.img(self.disputed)[0], self.feed("game=disputed"))

    def test_most_confident_first_keeps_the_chosen_filter(self):
        self.assertEqual(self.feed("game=reported&ordering=newest"), self.img(self.reported, self.reported_weak))
        self.assertEqual(self.feed("game=reported&ordering=game_confidence"),
                         self.img(self.reported_weak, self.reported))
        self.assertEqual(self.feed("game=disputed&ordering=game_confidence"), self.img(self.disputed))
        self.assertEqual(self.feed("game=applied&ordering=game_confidence"), self.img(self.applied))

    def test_all_images_most_confident_first_then_the_rest_newest_first(self):
        order = self.feed("ordering=game_confidence")
        self.assertEqual(order[:3], self.img(self.expert, self.waiting, self.reported_weak))
        self.assertEqual(set(order), set(self.feed("")))     # nothing is left out
        rest = [i for i in order[3:] if i in self.img(*self.named)]
        self.assertEqual(rest, self.img(self.plain, self.applied, self.disputed, self.reported))

    def test_proposal_filters_sort_as_before(self):
        self.assertEqual(self.feed("game=any&ordering=game_confidence"),
                         self.img(self.expert, self.waiting, self.reported_weak))
        self.assertEqual(self.feed("game=expert&ordering=game_confidence"), self.img(self.expert))

    def test_paging_runs_on_from_the_proposals_into_the_rest(self):
        pages, url = [], f"{URL}?ordering=game_confidence&page_size=2"
        while url:
            data = self.client.get(url).json()
            pages.append([r["image_asset_id"] for r in data["results"]])
            url = data["next"]
        self.assertEqual(pages[0], self.img(self.expert, self.waiting))
        self.assertEqual(pages[1][0], self.img(self.reported_weak)[0])
        everything = [i for page in pages for i in page]
        self.assertEqual(sorted(everything), sorted(self.feed("")))   # each image once
        self.assertEqual(len(everything), len(set(everything)))

    def test_the_dropdown_says_what_each_option_lists(self):
        page = self.client.get(reverse("tool_annotate")).content.decode()
        start = page.index('<select id="game-filter"')
        select = page[start:page.index("</select>", start)]
        for value, label in (("", "All images"), ("any", "Proposals waiting"), ("expert", "Expert-backed proposals"),
                             ("reported", "Reported by players"), ("disputed", "Disputed validated labels"),
                             ("applied", "Applied from the game")):
            self.assertIn(f'<option value="{value}">{label}</option>', select)
        self.assertEqual(select.count("<option"), 6)
        self.assertIn("Accepted or rejected proposals come back when new answers arrive", page)
        self.assertNotIn("dismissed drop out", page)
