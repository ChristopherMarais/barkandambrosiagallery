"""
Tests for the Beetle ID game: scoring rules, round building, the round API
(including that it never reveals which items are scored), and staff consensus.
"""
import json

from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from beetlesgallery.beetles_app import game
from beetlesgallery.beetles_app.models import GameAnswer, GameRound, Taxon
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_image, make_taxon


def taxon(subfamily="Scolytinae", tribe="Xyleborini", genus="Xyleborus", species="affinis"):
    return Taxon(subfamily=subfamily, tribe=tribe, genus=genus, species=species)


class ScoringTests(SimpleTestCase):
    def test_classification_scores_each_answered_rank(self):
        answer = {"subfamily": "scolytinae", "tribe": "Xyleborini", "genus": "Xylosandrus", "species": ""}
        self.assertEqual(game.score_classification(answer, taxon()), {
            "subfamily": True, "tribe": True, "genus": False, "species": None,
        })

    def test_species_needs_the_right_genus(self):
        answer = {"genus": "Xylosandrus", "species": "affinis"}
        self.assertFalse(game.score_classification(answer, taxon())["species"])

    def test_rank_missing_from_reference_is_not_scored(self):
        answer = {"subfamily": "Scolytinae", "tribe": "Anything"}
        self.assertIsNone(game.score_classification(answer, taxon(tribe=None))["tribe"])

    def test_pair_same_genus_claims_ranks_above_and_not_species(self):
        a, b = taxon(), taxon(species="ferrugineus")
        self.assertEqual(game.score_pair("genus", a, b), {
            "subfamily": True, "tribe": True, "genus": True, "species": True,
        })
        self.assertEqual(game.score_pair("species", a, b)["species"], False)
        self.assertEqual(game.score_pair("tribe", a, b), {   # nothing judged below the first mistake (#530)
            "subfamily": True, "tribe": True, "genus": False, "species": None,
        })

    def test_pair_different_subfamily(self):
        a, b = taxon(), taxon("Platypodinae", "Platypodini", "Platypus", "cylindrus")
        self.assertTrue(all(game.score_pair("different", a, b).values()))
        self.assertEqual(game.score_pair("subfamily", a, b)["subfamily"], False)

    def test_pair_unsure_is_not_scored(self):
        self.assertEqual(set(game.score_pair("unsure", taxon(), taxon()).values()), {None})

    def test_shared_deeper_rank_implies_blank_higher_rank(self):
        a, b = taxon(tribe=None), taxon(tribe="Xyleborini")
        self.assertTrue(game.shared_ranks(a, b)["tribe"])


# Most game tests answer at species with a brand-new player: every rank open (the rank steps have their own tests)
@override_settings(GAME_RANK_UNLOCK_ANSWERS={"tribe": 0, "genus": 0, "species": 0})
class GameCase(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        self.t_affinis = make_taxon(subfamily="Scolytinae", tribe="Xyleborini", genus="Xyleborus",
                                    species="affinis", scientific_name="Xyleborus affinis")
        self.t_ferr = make_taxon(subfamily="Scolytinae", tribe="Xyleborini", genus="Xyleborus",
                                 species="ferrugineus", scientific_name="Xyleborus ferrugineus")
        self.t_plat = make_taxon(subfamily="Platypodinae", tribe="Platypodini", genus="Platypus",
                                 species="cylindrus", scientific_name="Platypus cylindrus")

    def roi(self, taxon=None, validated=True):
        image = make_image(image_file="originals/aa/bb/test.jpg")
        return make_beetle(image=image, taxon=taxon, bbox="validated" if validated else "unvalidated")

    def post(self, name, body, *args):
        return self.client.post(reverse(name, args=args), json.dumps(body), content_type="application/json")

    def play(self, mode):
        self.client.force_login(self.user)
        res = self.post("game_start", {"mode": mode})
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        return GameRound.objects.get(id=data["round"]), data["item"]


class GamePageTests(GameCase):
    def test_pages_need_login(self):
        for url in [reverse("game_home"), reverse("game_play", args=["classify"])]:
            with self.subTest(url=url):
                self.assertRedirectsToLogin(self.client.get(url))

    def test_pages_load_for_player(self):
        self.client.force_login(self.user)
        for url in [reverse("game_home"), reverse("game_home") + "?sort=accuracy",
                    reverse("game_play", args=["classify"]), reverse("game_play", args=["pair"])]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_unknown_mode_is_404(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("game_play", args=["nope"])).status_code, 404)

    def test_landing_links_to_game(self):
        self.assertContains(self.client.get(reverse("image_browser")), reverse("game_home"))

    def test_review_is_superuser_only(self):
        self.assertRedirectsToLogin(self.client.get(reverse("game_review")))
        for account in (self.user, self.staff):
            self.client.force_login(account)
            self.assertEqual(self.client.get(reverse("game_review")).status_code, 404)
            self.assertEqual(self.client.get(reverse("game_export", args=["labels"])).status_code, 404)
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(reverse("game_review")).status_code, 200)
        for kind in ["labels", "players"]:
            res = self.client.get(reverse("game_export", args=[kind]))
            self.assertEqual(res["Content-Type"], "text/csv")
        self.assertEqual(self.client.get(reverse("game_export", args=["x"])).status_code, 404)


class RoundBuildingTests(GameCase):
    def test_no_playable_items(self):
        self.client.force_login(self.user)
        self.assertEqual(self.post("game_start", {"mode": "classify"}).status_code, 404)

    def test_rois_without_boxes_or_deleted_are_not_used(self):
        make_beetle(image=make_image(image_file="x.jpg"), taxon=self.t_affinis)  # no bbox
        gone = self.roi(self.t_affinis)
        gone.delete()
        self.assertFalse(game.playable_rois().exists())

    @override_settings(GAME_ROUND_SIZE=10)
    def test_new_player_gets_more_checks(self):
        for _ in range(10):
            self.roi(self.t_affinis)
            self.roi(validated=False)
        rnd = game.start_round(self.user, "classify")
        self.assertEqual(sum(i["check"] for i in rnd.items), 6)

    @override_settings(GAME_ROUND_SIZE=10, GAME_CALIBRATION_CHECKS=0)
    def test_calibrated_player_gets_fewer_checks(self):
        for _ in range(10):
            self.roi(self.t_affinis)
            self.roi(validated=False)
        rnd = game.start_round(self.user, "classify")
        self.assertEqual(sum(i["check"] for i in rnd.items), 2)

    @override_settings(GAME_ROUND_SIZE=4)
    def test_short_pool_is_topped_up_from_the_other(self):
        for _ in range(4):
            self.roi(self.t_affinis)
        rnd = game.start_round(self.user, "classify")
        self.assertEqual(len(rnd.items), 4)
        self.assertTrue(all(i["check"] for i in rnd.items))

    @override_settings(GAME_ROUND_SIZE=6)
    def test_pairs_use_a_validated_partner(self):
        validated = {str(self.roi(t).id) for t in [self.t_affinis, self.t_ferr, self.t_plat] * 2}
        for _ in range(3):
            self.roi(self.t_affinis, validated=False)
        rnd = game.start_round(self.user, "pair")
        self.assertTrue(rnd.items)
        for item in rnd.items:
            self.assertIn(item["b"], validated)
            self.assertNotEqual(item["a"], item["b"])
            self.assertEqual(item["a"] in validated, item["check"])


class ClassifyApiTests(GameCase):
    @override_settings(GAME_ROUND_SIZE=2, GAME_MIN_JUDGED_FOR_ACCURACY=1)
    def test_round_scores_checks_only_and_reveals_nothing(self):
        check = self.roi(self.t_affinis)
        open_roi = self.roi(self.t_ferr, validated=False)
        rnd, item = self.play("classify")

        # The payload must not say which ROI this is or whether it is scored.
        self.assertEqual(set(item), {"index", "mode", "position", "total", "images", "prefetch"})
        self.assertEqual(set(item["images"][0]), {"url", "box", "small", "large"})   # the crops: #494
        self.assertNotIn(str(check.id), json.dumps(item))
        self.assertEqual(len(item["prefetch"]), 2)  # the other item's crops, small and large

        answer = {"subfamily": "Scolytinae", "tribe": "Xyleborini", "genus": "Xyleborus", "species": "affinis"}
        res = self.post("game_answer", dict(answer, index=item["index"]), rnd.id)
        self.assertEqual(res.status_code, 200)
        self.assertNotIn("correct", json.dumps(res.json()))
        nxt = res.json()["item"]

        res = self.post("game_answer", dict(answer, index=nxt["index"]), rnd.id)
        data = res.json()
        self.assertTrue(data["done"])
        self.assertEqual(data["summary"]["round_labelled"], 2)

        scored = GameAnswer.objects.get(roi=check)
        unscored = GameAnswer.objects.get(roi=open_roi)
        self.assertTrue(scored.is_check)
        self.assertTrue(scored.correct_species)
        self.assertFalse(unscored.is_check)
        self.assertIsNone(unscored.correct_species)
        self.assertEqual(data["summary"]["accuracy"], 1.0)
        rnd.refresh_from_db()
        self.assertIsNotNone(rnd.finished_at)

    @override_settings(GAME_ROUND_SIZE=1)
    def test_answer_must_match_the_taxonomy(self):
        self.roi(self.t_affinis)
        rnd, item = self.play("classify")
        for bad in [{"genus": "Madeupus"}, {"species": "affinis"}, {"genus": "Platypus", "tribe": "Xyleborini"}, {}]:
            with self.subTest(answer=bad):
                res = self.post("game_answer", dict(bad, index=item["index"]), rnd.id)
                self.assertEqual(res.status_code, 400)
        self.assertFalse(GameAnswer.objects.exists())

    @override_settings(GAME_ROUND_SIZE=1)
    def test_partial_answer_and_skip(self):
        self.roi(self.t_affinis)
        self.roi(self.t_affinis)  # scored items never repeat, so the second round needs another
        rnd, item = self.play("classify")
        res = self.post("game_answer", {"index": item["index"], "skipped": True}, rnd.id)
        self.assertNotIn("done", res.json())           # the feed carries on into a new batch
        self.assertNotEqual(res.json()["round"], str(rnd.id))
        self.assertTrue(GameAnswer.objects.get().skipped)

        rnd, item = self.play("classify")              # a reload picks the new batch up
        self.post("game_answer", {"index": item["index"], "subfamily": "Scolytinae"}, rnd.id)
        ans = GameAnswer.objects.get(round=rnd)
        self.assertEqual((ans.correct_subfamily, ans.correct_genus), (True, None))

    @override_settings(GAME_ROUND_SIZE=2)
    def test_out_of_step_and_other_players_rounds(self):
        self.roi(self.t_affinis)
        self.roi(self.t_ferr)
        rnd, item = self.play("classify")
        res = self.post("game_answer", {"index": item["index"] + 1, "genus": "Xyleborus"}, rnd.id)
        self.assertEqual(res.status_code, 409)

        self.client.force_login(self.staff)
        res = self.post("game_answer", {"index": item["index"], "genus": "Xyleborus"}, rnd.id)
        self.assertEqual(res.status_code, 404)


class PairApiTests(GameCase):
    @override_settings(GAME_ROUND_SIZE=1)
    def test_pair_check_is_scored(self):
        self.roi(self.t_affinis)
        self.roi(self.t_ferr)
        rnd, item = self.play("pair")
        self.assertEqual(len(item["images"]), 2)
        res = self.post("game_answer", {"index": item["index"], "pair_answer": "genus"}, rnd.id)
        self.assertTrue(res.json()["done"])
        ans = GameAnswer.objects.get()
        self.assertTrue(ans.is_check)
        self.assertEqual(
            [ans.correct_subfamily, ans.correct_tribe, ans.correct_genus, ans.correct_species],
            [True, True, True, True],
        )

    @override_settings(GAME_ROUND_SIZE=1)
    def test_pair_needs_a_valid_choice(self):
        self.roi(self.t_affinis)
        self.roi(self.t_ferr)
        rnd, item = self.play("pair")
        res = self.post("game_answer", {"index": item["index"], "pair_answer": "cousin"}, rnd.id)
        self.assertEqual(res.status_code, 400)


class TaxaApiTests(GameCase):
    def test_cascading_options(self):
        self.client.force_login(self.user)
        url = reverse("game_taxa")
        subfamilies = self.client.get(url, {"rank": "subfamily"}).json()["options"]
        self.assertEqual([o["value"] for o in subfamilies], ["Platypodinae", "Scolytinae"])

        genera = self.client.get(url, {"rank": "genus", "subfamily": "Scolytinae"}).json()["options"]
        self.assertEqual(genera, [{"value": "Xyleborus", "subfamily": "Scolytinae", "tribe": "Xyleborini"}])

        species = self.client.get(url, {"rank": "species", "genus": "Xyleborus"}).json()["options"]
        self.assertEqual([o["value"] for o in species], ["affinis", "ferrugineus"])
        self.assertEqual(self.client.get(url, {"rank": "species"}).json()["options"], [])
        self.assertEqual(self.client.get(url, {"rank": "kingdom"}).status_code, 400)

    def test_no_global_search(self):
        # Each list has its own search box in the page; there is no jump-to-any-name search.
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/game/api/taxa/search/", {"q": "xyl"}).status_code, 404)


class ConsensusTests(GameCase):
    def test_classify_and_pair_votes_combine(self):
        target = self.roi(self.t_plat, validated=False)
        partner = self.roi(self.t_affinis)
        rnd = GameRound.objects.create(player=self.user, mode="classify", items=[])
        GameAnswer.objects.create(round=rnd, player=self.user, mode="classify", index=0, roi=target,
                                  subfamily="Scolytinae", tribe="Xyleborini", genus="Xyleborus", species="affinis")
        prnd = GameRound.objects.create(player=self.staff, mode="pair", items=[])
        GameAnswer.objects.create(round=prnd, player=self.staff, mode="pair", index=0, roi=target,
                                  roi_b=partner, pair_answer="genus")
        GameAnswer.objects.create(round=prnd, player=self.staff, mode="pair", index=1, roi=target,
                                  roi_b=partner, pair_answer="different")

        [entry] = game.consensus()
        self.assertEqual(entry["answers"], 3)
        self.assertEqual(entry["ranks"]["genus"]["value"], "Xyleborus")
        self.assertEqual(entry["ranks"]["genus"]["votes"], 2)
        self.assertEqual(entry["ranks"]["species"]["value"], "Xyleborus affinis")
        self.assertEqual(entry["ranks"]["species"]["votes"], 1)

    def test_reliable_player_outweighs_unreliable(self):
        target = self.roi(validated=False)
        check = self.roi(self.t_affinis)
        good = GameRound.objects.create(player=self.user, mode="classify", items=[])
        bad = GameRound.objects.create(player=self.staff, mode="classify", items=[])
        for i in range(5):
            GameAnswer.objects.create(round=good, player=self.user, mode="classify", index=i, roi=check,
                                      is_check=True, genus="Xyleborus", correct_genus=True)
            GameAnswer.objects.create(round=bad, player=self.staff, mode="classify", index=i, roi=check,
                                      is_check=True, genus="Platypus", correct_genus=False)
        GameAnswer.objects.create(round=good, player=self.user, mode="classify", index=10, roi=target, genus="Xyleborus")
        GameAnswer.objects.create(round=bad, player=self.staff, mode="classify", index=10, roi=target, genus="Platypus")

        [entry] = game.consensus()
        self.assertEqual(entry["ranks"]["genus"]["value"], "Xyleborus")
        self.assertGreater(entry["ranks"]["genus"]["support"], 0.8)

        self.client.force_login(self.superuser)
        csv_text = self.client.get(reverse("game_export", args=["labels"])).content.decode()
        self.assertIn(str(target.id), csv_text)


# ---------------------------------------------------------------------------
# Expertise, trusted proposals, difficulty and rounds
# ---------------------------------------------------------------------------
from unittest import mock  # noqa: E402

from beetlesgallery.beetles_app import game_trust  # noqa: E402
from beetlesgallery.beetles_app.models import ImageLock, LabelReview, PlayerSkill, RoiDifficulty  # noqa: E402

# Small thresholds so a handful of answers proves competence: 3 images per species, 3 answers in all.
SMALL_TRUST = dict(GAME_TRUST_MIN_JUDGED=3, GAME_TRUST_IMAGES_PER_SPECIES=3)


class CoverageTests(SimpleTestCase):
    """Proof scales with the taxon: so many images of 75% of its children (all of them up to three), 90% right."""

    def test_a_small_genus_needs_few_answers_and_a_big_one_many(self):
        small = {"a x": 20, "a y": 20}
        big = {f"b {i}": 20 for i in range(40)}
        self.assertEqual(game_trust.coverage(small, {})["required"], 10)
        self.assertEqual(game_trust.coverage(big, {})["required"], 150)   # 30 of the 40 species (#381)

    def test_a_taxon_with_three_children_needs_all_of_them(self):
        available = {"a x": 20, "a y": 20, "a z": 2}
        lopsided = game_trust.coverage(available, {"a x": 30})
        self.assertFalse(lopsided["complete"])
        self.assertEqual((lopsided["children_done"], lopsided["children_needed"], lopsided["children_total"]), (1, 3, 3))
        full = game_trust.coverage(available, {"a x": 5, "a y": 5, "a z": 2})   # a z only has 2 images
        self.assertTrue(full["complete"])
        self.assertEqual(full["required"], 12)

    def test_accuracy_must_be_ninety_percent(self):
        cover = game_trust.coverage({"a x": 20, "a y": 20}, {"a x": 5, "a y": 5})
        self.assertTrue(game_trust.is_proven(9, 10, cover))
        self.assertFalse(game_trust.is_proven(8, 10, cover))

    def test_a_taxon_with_very_few_images_cannot_make_an_expert(self):
        cover = game_trust.coverage({"a x": 3}, {"a x": 3})
        self.assertFalse(game_trust.is_proven(3, 3, cover))


@override_settings(**SMALL_TRUST)
class TrustCase(GameCase):
    def setUp(self):
        super().setUp()
        self.t_xylo = make_taxon(subfamily="Scolytinae", tribe="Xyleborini", genus="Xylosandrus",
                                 species="crassiusculus", scientific_name="Xylosandrus crassiusculus")
        self.t_ambro = make_taxon(subfamily="Scolytinae", tribe="Xyleborini", genus="Ambrosiodmus",
                                  species="minor", scientific_name="Ambrosiodmus minor")

    def answer(self, player, roi, taxon=None, check=True, correct=True, **given):
        rnd = GameRound.objects.create(player=player, mode="classify", items=[])
        fields = dict(given)
        if check:
            ref = roi.taxon
            fields.update(ref_subfamily=ref.subfamily, ref_tribe=ref.tribe, ref_genus=ref.genus, ref_species=ref.species)
            for r in game.RANKS:
                fields[f"correct_{r}"] = correct
        return GameAnswer.objects.create(round=rnd, player=player, mode="classify", index=0, roi=roi,
                                         is_check=check, **fields)

    def prove(self, player, taxon, n=3, correct=True):
        for _ in range(n):
            self.answer(player, self.roi(taxon), correct=correct)
        game_trust.recompute_skills(player)

    def label(self, player, roi, taxon):
        return self.answer(player, roi, check=False, subfamily=taxon.subfamily, tribe=taxon.tribe,
                           genus=taxon.genus, species=taxon.species)



class TrustTests(TrustCase):
    def test_skills_are_per_rank_and_branch(self):
        self.prove(self.user, self.t_affinis)
        skills = {(s.rank, s.branch): s for s in PlayerSkill.objects.filter(player=self.user)}
        self.assertTrue(skills[("species", "Xyleborus")].proven)
        self.assertTrue(skills[("genus", "Xyleborini")].proven)
        self.assertTrue(skills[("tribe", "Scolytinae")].proven)
        self.assertTrue(skills[("subfamily", "")].proven)
        self.assertIsNotNone(skills[("species", "Xyleborus")].proven_at)

    def test_wrong_answers_do_not_prove(self):
        self.prove(self.user, self.t_affinis, correct=False)
        self.assertFalse(PlayerSkill.objects.filter(player=self.user, proven=True).exists())

    def test_replaying_the_same_roi_counts_once(self):
        roi = self.roi(self.t_affinis)
        for _ in range(5):
            self.answer(self.user, roi)
        game_trust.recompute_skills(self.user)
        skill = PlayerSkill.objects.get(player=self.user, rank="species", branch="Xyleborus")
        self.assertEqual((skill.judged, skill.proven), (1, False))

    def test_expert_label_is_trusted_down_to_species(self):
        self.prove(self.user, self.t_affinis)
        target = self.roi(validated=False)
        self.label(self.user, target, self.t_ferr)
        [entry] = game.consensus(roi_ids=[target.id])
        self.assertEqual(entry["trusted_rank"], "species")
        self.assertEqual(entry["taxon"], self.t_ferr)

    def test_non_expert_label_is_not_trusted(self):
        target = self.roi(validated=False)
        self.label(self.user, target, self.t_ferr)
        [entry] = game.consensus(roi_ids=[target.id])
        self.assertEqual(entry["trusted_rank"], "")
        self.assertFalse(entry["ranks"]["genus"]["trusted"])

    def test_expertise_in_another_testable_genus_does_not_carry_over(self):
        self.prove(self.user, self.t_plat)  # Platypus expert only
        for _ in range(3):
            self.roi(self.t_affinis)  # Xyleborus is testable
        target = self.roi(validated=False)
        self.label(self.user, target, self.t_affinis)
        [entry] = game.consensus(roi_ids=[target.id])
        # Subfamily-level skill is overall, so that part is trusted; nothing below it.
        self.assertEqual(entry["trusted_rank"], "subfamily")

    def test_expert_disagreement_blocks_trust(self):
        self.prove(self.user, self.t_affinis)
        self.prove(self.staff, self.t_affinis)
        target = self.roi(validated=False)
        self.label(self.user, target, self.t_affinis)
        self.label(self.staff, target, self.t_ferr)
        [entry] = game.consensus(roi_ids=[target.id])
        self.assertEqual(entry["trusted_rank"], "genus")

    def test_untestable_genus_trusted_via_sibling_genera(self):
        # Xylosandrus has no validated ROIs, so it can't be tested directly.
        self.prove(self.user, self.t_affinis)  # Xyleborus
        target = self.roi(validated=False)
        self.label(self.user, target, self.t_xylo)
        [entry] = game.consensus(roi_ids=[target.id])
        self.assertEqual(entry["trusted_rank"], "genus")  # one sibling genus is not enough

        self.prove(self.user, self.t_ambro)  # second genus in Xyleborini
        from django.core.cache import cache
        cache.clear()
        [entry] = game.consensus(roi_ids=[target.id])
        self.assertEqual(entry["trusted_rank"], "species")


class ProposalApiTests(TrustCase):
    def test_proposals_for_image_and_accept(self):
        self.prove(self.user, self.t_affinis)
        target = self.roi(validated=False)
        self.label(self.user, target, self.t_ferr)
        url = reverse("game_proposals")

        self.client.force_login(self.user)
        self.assertRedirectsToLogin(self.client.get(url, {"image_asset": target.image_asset_id}))

        self.client.force_login(self.staff)
        data = self.client.get(url, {"image_asset": target.image_asset_id}).json()["proposals"]
        proposal = data[str(target.id)]
        self.assertEqual(proposal["trusted_rank"], "species")
        self.assertEqual(proposal["taxon"]["valid_species_id"], self.t_ferr.valid_species_id)
        self.assertIsNone(proposal["review"])

        res = self.post("game_proposal_review", {"decision": "accept"}, target.id)
        self.assertEqual(res.status_code, 200, res.content)
        target.refresh_from_db()
        self.assertEqual(target.taxon, self.t_ferr)
        self.assertFalse(target.bbox_is_validated)
        review = LabelReview.objects.get()
        self.assertEqual((review.decision, review.reviewed_by, review.trusted_rank), ("accepted", self.staff, "species"))

        proposal = self.client.get(url, {"image_asset": target.image_asset_id}).json()["proposals"][str(target.id)]
        self.assertEqual(proposal["review"]["decision"], "accepted")

    def test_dismiss_and_genus_only(self):
        from beetlesgallery.beetles_app.models import PlayerScore
        PlayerScore.objects.create(player=self.user, score=1600, rating=0.75)   # at the suggestions level
        target = self.roi(validated=False)
        self.answer(self.user, target, check=False, subfamily="Scolytinae", tribe="Xyleborini", genus="Xyleborus")
        self.client.force_login(self.staff)
        self.assertEqual(self.post("game_proposal_review", {"decision": "accept"}, target.id).status_code, 400)
        self.assertEqual(self.post("game_proposal_review", {"decision": "dismiss"}, target.id).status_code, 200)
        self.assertEqual(LabelReview.objects.get().decision, "dismissed")

    def test_locked_image_is_refused(self):
        target = self.roi(validated=False)
        self.label(self.user, target, self.t_ferr)
        ImageLock.objects.create(image_asset=target.image_asset, locked_by=self.superuser)
        self.client.force_login(self.staff)
        self.assertEqual(self.post("game_proposal_review", {"decision": "accept"}, target.id).status_code, 409)

    def test_annotation_page_loads_proposal_ui(self):
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("tool_annotate")), "loadGameProposals")


class DifficultyTests(GameCase):
    def test_game_difficulty_from_answers(self):
        roi = self.roi(self.t_affinis)
        rnd = GameRound.objects.create(player=self.user, mode="classify", items=[])
        for i in range(4):
            GameAnswer.objects.create(round=rnd, player=self.user, mode="classify", index=i, roi=roi,
                                      is_check=True, genus="Platypus", correct_genus=False)
        game.update_difficulty([roi.id])
        self.assertGreater(RoiDifficulty.objects.get(roi=roi).game_difficulty, 0.7)

    def test_model_difficulty_wins(self):
        roi = self.roi(self.t_affinis)
        diff = RoiDifficulty.objects.create(roi=roi, model_difficulty=0.1, game_difficulty=0.9)
        self.assertEqual(diff.value, 0.1)

    def test_target_rises_with_rounds(self):
        start = game.target_difficulty(self.user)
        for _ in range(5):
            GameRound.objects.create(player=self.user, mode="classify", items=[], finished_at=timezone.now())
        self.assertGreater(game.target_difficulty(self.user), start)

    def test_experts_get_harder_beetles_than_novices(self):
        from beetlesgallery.beetles_app.models import PlayerScore
        novice = game.target_difficulty(self.user)
        PlayerScore.objects.create(player=self.staff, rating=0.85)
        self.assertGreater(game.target_difficulty(self.staff), novice + 0.4)

    def test_experts_get_closer_relatives_in_family_ties(self):
        import random
        random.seed(1)
        hard = [game._relation_order(0.85)[0] for _ in range(200)]
        easy = [game._relation_order(0.1)[0] for _ in range(200)]
        self.assertGreater(hard.count("species") + hard.count("genus"), 120)
        self.assertGreater(easy.count("different") + easy.count("subfamily"), 120)

    def test_pick_near_prefers_matching_difficulty(self):
        easy = [self.roi(self.t_affinis) for _ in range(5)]
        hard = [self.roi(self.t_affinis) for _ in range(5)]
        for r in easy:
            RoiDifficulty.objects.create(roi=r, model_difficulty=0.05)
        for r in hard:
            RoiDifficulty.objects.create(roi=r, model_difficulty=0.95)
        ids = [r.id for r in easy + hard]

        # The draw is random (and picks a far-off item about 1 time in 80 by chance), so make it take
        # the heaviest candidate each time: what is under test is the weighting, not the luck.
        def heaviest(pool, weights):
            return [pool[weights.index(max(weights))]]

        with mock.patch.object(game.random, "choices", side_effect=heaviest):
            self.assertEqual(set(game._pick_near(ids, 5, target=0.1)), {r.id for r in easy})
            self.assertEqual(set(game._pick_near(ids, 5, target=0.9)), {r.id for r in hard})


class RoundFlowTests(GameCase):
    @override_settings(GAME_ROUND_SIZE=3)
    def test_reload_resumes_the_round(self):
        for _ in range(3):
            self.roi(self.t_affinis)
        rnd, item = self.play("classify")
        self.post("game_answer", {"index": item["index"], "genus": "Xyleborus"}, rnd.id)
        again, item2 = self.play("classify")
        self.assertEqual(again.id, rnd.id)
        self.assertEqual(item2["position"], 2)

    def test_focus_targets_branches_the_player_labels(self):
        self.assertIsNone(game.focus_filter(self.user))
        rnd = GameRound.objects.create(player=self.user, mode="classify", items=[])
        GameAnswer.objects.create(round=rnd, player=self.user, mode="classify", index=0,
                                  roi=self.roi(validated=False), tribe="Xyleborini", genus="Xyleborus")
        focused = game.check_rois().filter(game.focus_filter(self.user))
        inside, outside = self.roi(self.t_affinis), self.roi(self.t_plat)
        self.assertIn(inside, focused)
        self.assertNotIn(outside, focused)

    def test_malformed_taxa_are_not_offered(self):
        # A shifted row from the real species list: epithet in the subfamily column.
        make_taxon(subfamily="alienus", tribe="", genus="", species="", scientific_name="Glochiphorus alienus")
        self.client.force_login(self.user)
        options = self.client.get(reverse("game_taxa"), {"rank": "subfamily"}).json()["options"]
        self.assertNotIn("alienus", [o["value"] for o in options])

    def test_response_time_is_recorded(self):
        self.roi(self.t_affinis)
        with override_settings(GAME_ROUND_SIZE=1):
            rnd, item = self.play("classify")
        self.post("game_answer", {"index": item["index"], "genus": "Xyleborus", "elapsed_ms": 4200}, rnd.id)
        self.assertEqual(GameAnswer.objects.get().response_ms, 4200)


class ReportTests(GameCase):
    def test_own_report(self):
        self.client.force_login(self.user)
        self.assertRedirectsToLogin(self.client.get(reverse("game_player_report", args=[self.staff.id])))
        res = self.client.get(reverse("game_report"))
        self.assertContains(res, "My performance")

    def test_staff_can_view_any_report(self):
        self.client.force_login(self.staff)
        res = self.client.get(reverse("game_player_report", args=[self.user.id]))
        self.assertContains(res, self.user.username)
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(reverse("game_export", args=["skills"]))["Content-Type"], "text/csv")


@override_settings(**SMALL_TRUST, GAME_REPORT_MIN_JUDGED=2)
class ReportPrivacyTests(TrustCase):
    def test_report_only_shows_groups_the_player_named(self):
        # Scored items were Xyleborus; the player called them Platypus every time.
        for _ in range(2):
            self.answer(self.user, self.roi(self.t_affinis), correct=False, subfamily="Platypodinae",
                        tribe="Platypodini", genus="Platypus")
        game_trust.recompute_skills(self.user)
        progressing = game_trust.player_report(self.user)["progressing"]
        self.assertNotIn("Xyleborus", [s.branch for s in progressing])
        self.assertEqual([s.rank for s in progressing], ["subfamily"])


# ---------------------------------------------------------------------------
# Round feedback and player reports
# ---------------------------------------------------------------------------
from beetlesgallery.beetles_app import game_feedback  # noqa: E402
from beetlesgallery.beetles_app.models import GameReport  # noqa: E402


class FeedbackCase(GameCase):
    def finished_round(self, player, answers):
        """answers: [(roi, is_check, {fields})]; scores check answers like the API does."""
        rnd = GameRound.objects.create(
            player=player, mode="classify", finished_at=timezone.now(),
            items=[{"a": str(r.id), "b": None, "check": c} for r, c, _ in answers],
        )
        for i, (roi, check, fields) in enumerate(answers):
            ans = GameAnswer(round=rnd, player=player, mode="classify", index=i, roi=roi, is_check=check, **fields)
            if check:
                for r, ok in game.score_classification(fields, roi.taxon).items():
                    setattr(ans, f"correct_{r}", ok)
                t = roi.taxon
                ans.ref_subfamily, ans.ref_tribe, ans.ref_genus, ans.ref_species = t.subfamily, t.tribe, t.genus, t.species
            ans.save()
        return rnd


AFFINIS = {"subfamily": "Scolytinae", "tribe": "Xyleborini", "genus": "Xyleborus", "species": "affinis"}
FERR = dict(AFFINIS, species="ferrugineus")


class FeedbackTests(FeedbackCase):
    def test_feedback_shows_results_and_database_labels(self):
        check = self.roi(self.t_affinis)
        wrong = self.roi(self.t_affinis)
        unverified = self.roi(self.t_ferr, validated=False)
        rnd = self.finished_round(self.user, [(check, True, AFFINIS), (wrong, True, FERR), (unverified, False, AFFINIS)])
        fb = game_feedback.round_feedback(rnd)
        self.assertEqual((fb["right"], fb["scored"]), (1, 2))
        first, second, third = fb["items"]
        self.assertEqual(first["verdict"], "right")
        self.assertEqual(second["verdict"], "wrong")   # a wrong species is wrong, not partly correct (#530)
        self.assertFalse(second["results"]["species"])
        self.assertTrue(second["sides"][0]["verified"])
        self.assertEqual(second["sides"][0]["label"]["species"], "Xyleborus affinis")
        # Unverified: no verdict, the unverified label, and what players said.
        self.assertIsNone(third["verdict"])
        self.assertFalse(third["sides"][0]["verified"])
        self.assertEqual(third["sides"][0]["label"]["species"], "Xyleborus ferrugineus")
        self.assertEqual(third["sides"][0]["others"]["value"], "Xyleborus affinis")

    def test_review_page_access(self):
        rnd = self.finished_round(self.user, [(self.roi(self.t_affinis), True, AFFINIS)])
        url = reverse("game_round_review", args=[rnd.id])
        self.assertRedirectsToLogin(self.client.get(url))
        self.client.force_login(self.user)
        self.assertContains(self.client.get(url), "Your answers")
        self.client.force_login(self.superuser)  # staff may look
        self.assertEqual(self.client.get(url).status_code, 200)
        other = get_user_model_for_tests().objects.create_user("other", password="pw")
        self.client.force_login(other)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_unfinished_round_redirects_to_play(self):
        rnd = GameRound.objects.create(player=self.user, mode="classify", items=[])
        self.client.force_login(self.user)
        res = self.client.get(reverse("game_round_review", args=[rnd.id]))
        self.assertRedirects(res, reverse("game_play", args=["classify"]), fetch_redirect_response=False)

    @override_settings(GAME_ROUND_SIZE=2)
    def test_revealed_items_are_never_scored_again(self):
        seen = self.roi(self.t_affinis)
        self.finished_round(self.user, [(seen, True, AFFINIS)])
        fresh = self.roi(self.t_ferr)
        partner_seen = self.roi(self.t_plat)
        GameAnswer.objects.create(round=GameRound.objects.create(player=self.user, mode="pair", items=[]),
                                  player=self.user, mode="pair", index=0, roi=self.roi(validated=False),
                                  roi_b=partner_seen, pair_answer="different")
        rnd = game.start_round(self.user, "classify")
        checks = {i["a"] for i in rnd.items if i["check"]}
        self.assertEqual(checks, {str(fresh.id)})

    def test_finish_returns_review_url(self):
        self.roi(self.t_affinis)
        with override_settings(GAME_ROUND_SIZE=1):
            rnd, item = self.play("classify")
        data = self.post("game_answer", dict(AFFINIS, index=item["index"]), rnd.id).json()
        self.assertEqual(data["review_url"], reverse("game_round_review", args=[rnd.id]))


def get_user_model_for_tests():
    from django.contrib.auth import get_user_model
    return get_user_model()


@override_settings(GAME_MIN_JUDGED_FOR_ACCURACY=1)
class PlayerReportTests(FeedbackCase):
    def setUp(self):
        super().setUp()
        self.bad = self.roi(self.t_affinis)  # actually a ferrugineus; the reference is wrong
        # The reporter answered correctly (ferrugineus) and was marked wrong; staff matched the bad label.
        self.rnd = self.finished_round(self.user, [(self.bad, True, FERR)])
        self.finished_round(self.staff, [(self.bad, True, AFFINIS)])
        self.client.force_login(self.user)

    def report(self, **body):
        payload = {"round": str(self.rnd.id), "index": 0, "roi": str(self.bad.id), "reason": "wrong_label", "note": "ferrugineus"}
        payload.update(body)
        return self.post("game_report_roi", payload)

    def test_report_holds_the_reporters_answer_and_quarantines_the_roi(self):
        self.assertEqual(game.player_summary(self.user)["accuracy"], 0.75)
        res = self.report()
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(GameAnswer.objects.get(player=self.user).score_hold)
        self.assertFalse(GameAnswer.objects.get(player=self.staff).score_hold)
        self.assertIsNone(game.player_summary(self.user)["accuracy"])  # nothing left to score
        self.assertNotIn(self.bad, game.check_rois())
        # Reporting twice keeps one open report.
        self.report()
        self.assertEqual(GameReport.objects.count(), 1)

    def test_report_validation(self):
        self.assertEqual(self.report(reason="nope").status_code, 400)
        self.assertEqual(self.report(roi=str(self.roi(self.t_affinis).id)).status_code, 400)
        self.assertEqual(self.report(index=5).status_code, 404)
        self.client.force_login(self.staff)
        self.assertEqual(self.report().status_code, 404)  # not their round

    def test_confirmed_releases_the_hold(self):
        self.report()
        game_feedback.resolve_reports(self.bad, "confirmed", self.superuser)
        self.assertFalse(GameAnswer.objects.get(player=self.user).score_hold)
        self.assertEqual(game.player_summary(self.user)["accuracy"], 0.75)
        self.assertEqual(GameReport.objects.get().status, "confirmed")
        self.assertIn(self.bad, game.check_rois())

    def test_corrected_rescores_everyone(self):
        self.report()
        self.bad.depicts_valid_name_id = self.t_ferr.valid_species_id
        self.bad.save()
        self.client.force_login(self.staff)
        res = self.post("game_resolve_reports", {"outcome": "corrected"}, self.bad.id)
        self.assertEqual(res.json()["closed"], 1)
        reporter = GameAnswer.objects.get(player=self.user)
        matcher = GameAnswer.objects.get(player=self.staff)
        self.assertFalse(reporter.score_hold)
        self.assertTrue(reporter.correct_species)
        self.assertEqual(reporter.ref_species, "ferrugineus")
        self.assertFalse(matcher.correct_species)
        self.assertEqual(game.player_summary(self.user)["accuracy"], 1.0)

    def test_corrected_by_unvalidating_voids_scores(self):
        self.report()
        self.bad.bbox_is_validated = False
        self.bad.save()
        game_feedback.resolve_reports(self.bad, "corrected", self.superuser)
        self.assertTrue(all(GameAnswer.objects.values_list("score_hold", flat=True)))

    def test_resolve_is_staff_only_and_annotator_shows_reports(self):
        self.report()
        self.assertRedirectsToLogin(self.post("game_resolve_reports", {"outcome": "confirmed"}, self.bad.id))
        self.client.force_login(self.staff)
        data = self.client.get(reverse("game_proposals"), {"image_asset": self.bad.image_asset_id}).json()
        self.assertEqual(data["reports"][str(self.bad.id)][0]["reason"], "The name looks wrong")
        page = self.client.get(reverse("tool_annotate"))
        self.assertContains(page, "gameReportsHtml")
        self.assertContains(page, "get('image')")  # ?image= deep link
        self.client.force_login(self.superuser)
        self.assertContains(self.client.get(reverse("game_review")), "Open in annotator")
