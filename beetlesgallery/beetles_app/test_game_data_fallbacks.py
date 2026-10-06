"""
Always something to play, whatever beetles the gallery has (#493). One extreme at a time: an empty gallery, only
unvalidated or only validated beetles, a single species, IBBI-AI predictions but no grid to build, a focus with nothing
in it, a player who has seen everything, a chosen game that is locked. The player gets a beetle, or a message that says
why in plain words: never the old "no images ready" dead end while something is playable.

The grid games' own builders are another PR's (#489): where a test needs a grid game that can build nothing, it stubs
the builder out, so it holds whatever those builders do.
"""
from unittest import mock

from django.test import override_settings
from django.urls import reverse

from beetlesgallery.beetles_app import game
from beetlesgallery.beetles_app.models import Beetles, GameAnswer, GamePreference, GameRound, ModelPrediction
from beetlesgallery.beetles_app.test_game import AFFINIS, GameCase
from beetlesgallery.beetles_app.testing import make_beetle, make_image

NO_BEETLES = "No beetles are ready for the game yet. Please check back soon."
SEEN_ALL = "You've seen every beetle we have. New photos are added regularly."
SEEN_FOCUS = "You've seen every beetle in your focus. Clear it to see more."


class FallbackCase(GameCase):
    def grant(self, *perks, play_mode="both", **focus):
        """Unlocks without a made-up score (finishing a batch recomputes the score from the real answers)."""
        GamePreference.objects.update_or_create(
            player=self.user, defaults=dict(granted_perks=list(perks), play_mode=play_mode, **focus))

    def start(self, **body):
        self.client.force_login(self.user)
        return self.post("game_start", dict({"mode": "mixed"}, **body))

    def started(self, **body):
        res = self.start(**body)
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def no_grids(self):
        """The grid games can build nothing (as on staging, where IBBI-AI's predictions asked too much of them)."""
        for builder in ("build_odd_items", "build_select_items"):
            self.enterContext(mock.patch.object(game, builder, return_value=[]))

    def answer(self, data, body):
        item = data["item"]
        return self.post("game_answer", dict(body, index=item["index"]), data["round"]).json()

    def answer_for(self, item):
        return AFFINIS if item["mode"] == "classify" else {"pair_answer": "genus"}


class EmptyGalleryTests(FallbackCase):
    def test_an_empty_gallery_says_there_are_no_beetles_yet(self):
        make_beetle(image=make_image(image_file="x.jpg"), taxon=self.t_affinis)   # a beetle without a box can't be shown
        res = self.start()
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["error"], NO_BEETLES)


class OnlyUnvalidatedTests(FallbackCase):
    def setUp(self):
        super().setUp()
        for taxon in (self.t_affinis, self.t_ferr, self.t_plat) * 3:
            self.roi(taxon, validated=False)

    def test_identification_plays_them_as_open_beetles(self):
        self.grant("identification", "choose_game", play_mode="classify")
        data = self.started()
        self.assertEqual((data["item"]["mode"], data["notice"]), ("classify", ""))
        items = GameRound.objects.get(id=data["round"]).items
        self.assertEqual(len(items), 9)
        self.assertFalse(any(i["check"] for i in items))

    def test_in_the_mix_identification_fills_in_for_similarity(self):
        self.grant("identification")   # Similarity and Identification, mixed: Similarity has no checked partners
        rnd = game.start_round(self.user, "mixed", size=6)
        self.assertEqual(({i["mode"] for i in rnd.items}, rnd.notice), ({"classify"}, ""))

    def test_similarity_chosen_falls_back_to_the_other_game_and_the_choice_is_kept(self):
        self.grant("identification", "choose_game", play_mode="pair")
        data = self.started()
        self.assertEqual(data["item"]["mode"], "classify")
        self.assertEqual(data["notice"], "Not enough beetles for Similarity right now: here's Identification instead.")
        self.assertEqual(data["prefs"]["play_mode"], "pair")
        self.assertEqual(GamePreference.objects.get(player=self.user).play_mode, "pair")

    def test_a_player_with_only_similarity_is_told_why(self):
        res = self.start()   # a new player: Similarity alone, and every pair needs a checked beetle
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["error"], "Not enough checked beetles for Similarity yet. Please check back soon.")

    def test_a_player_whose_games_all_need_checked_beetles_is_told_why(self):
        self.no_grids()
        self.grant("odd_one_out", "choose_game", play_mode="odd")
        res = self.start()
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["error"], "Not enough checked beetles for your games yet. Please check back soon.")


class OnlyValidatedTests(FallbackCase):
    def setUp(self):
        super().setUp()
        for taxon in (self.t_affinis, self.t_ferr, self.t_plat) * 3:
            self.roi(taxon)

    def test_identification_scores_every_beetle(self):
        rnd = game.start_round(self.user, "classify", size=6)
        self.assertEqual(len(rnd.items), 6)
        self.assertTrue(all(i["check"] for i in rnd.items))

    def test_similarity_pairs_checked_beetles(self):
        data = self.started()   # a new player: Similarity
        self.assertEqual(data["item"]["mode"], "pair")
        items = GameRound.objects.get(id=data["round"]).items
        self.assertTrue(all(i["check"] for i in items))
        validated = {str(i) for i in Beetles.objects.filter(bbox_is_validated=True).values_list("id", flat=True)}
        self.assertTrue(all({i["a"], i["b"]} <= validated for i in items))


class SingleSpeciesTests(FallbackCase):
    def setUp(self):
        super().setUp()
        for _ in range(4):
            self.roi(self.t_affinis)
            self.roi(self.t_affinis, validated=False)

    def test_similarity_pairs_beetles_of_the_one_species(self):
        data = self.started()
        self.assertEqual(data["item"]["mode"], "pair")

    def test_identification_works(self):
        self.assertTrue(game.start_round(self.user, "classify", size=4).items)

    def test_a_chosen_grid_game_never_dead_ends(self):
        # no grid can hold an odd one out of a single species; whatever the grid builder does, the player gets a beetle
        self.grant("odd_one_out", "choose_game", play_mode="odd")
        data = self.started()
        if data["item"]["mode"] != "odd":
            self.assertEqual(data["notice"], "Not enough beetles for Imposter Picker right now: here's Similarity instead.")


class PredictionsButNoGridTests(FallbackCase):
    """Staging: IBBI-AI predictions exist, the player picks a grid game, and it can build nothing."""

    def setUp(self):
        super().setUp()
        for taxon in (self.t_affinis, self.t_ferr, self.t_plat) * 3:
            self.roi(taxon)
            unchecked = self.roi(taxon, validated=False)
            ModelPrediction.objects.create(roi=unchecked, valid_species_id=taxon.valid_species_id, taxon=taxon,
                                           confidence=0.75, model_name="m", model_version="1")
        self.no_grids()
        self.grant("odd_one_out", "select_all", "choose_game", play_mode="odd")

    def test_the_chosen_grid_game_falls_back_to_a_mix_with_a_notice(self):
        data = self.started()
        self.assertEqual(data["item"]["mode"], "pair")
        self.assertEqual(data["notice"], "Not enough beetles for Imposter Picker right now: here's a mix of your other games.")
        self.assertEqual(data["prefs"]["play_mode"], "odd")   # the choice is kept: the next batch tries it again
        self.assertEqual(GamePreference.objects.get(player=self.user).play_mode, "odd")

    def test_a_reload_picks_the_batch_up_without_the_notice(self):
        first = self.started()
        again = self.started()
        self.assertEqual((again["round"], again["notice"]), (first["round"], ""))

    @override_settings(GAME_ROUND_SIZE=1)
    def test_the_next_batch_falls_back_too(self):
        data = self.started()
        res = self.answer(data, self.answer_for(data["item"]))
        self.assertNotIn("done", res)
        self.assertNotEqual(res["round"], data["round"])
        self.assertEqual(res["item"]["mode"], "pair")
        self.assertIn("Imposter Picker", res["notice"])

    def test_a_player_back_on_the_mix_needs_no_notice(self):
        GamePreference.objects.filter(player=self.user).update(play_mode="both")
        data = self.started()
        self.assertEqual((data["item"]["mode"], data["notice"]), ("pair", ""))


class FocusTests(FallbackCase):
    def focus(self, value, *perks):
        self.grant("focus_subfamily", *perks, focus_rank="subfamily", focus_value=value)
        self.assertEqual(game.player_focus(self.user), ("subfamily", value))

    def subfamilies(self, rnd):
        return {Beetles.objects.select_related("taxon").get(id=i["a"]).taxon.subfamily for i in rnd.items}

    def test_a_focus_with_no_beetles_falls_back_to_everything(self):
        for _ in range(3):
            self.roi(self.t_affinis)
            self.roi(self.t_ferr, validated=False)
        self.focus("Platypodinae")
        self.assertEqual(self.started()["item"]["mode"], "pair")

    def test_a_focus_with_only_checked_beetles_fills_the_batch_with_them(self):
        for _ in range(6):
            self.roi(self.t_plat)
            self.roi(self.t_affinis, validated=False)
        self.focus("Platypodinae")
        rnd = game.start_round(self.user, "classify", size=4)
        self.assertEqual((len(rnd.items), self.subfamilies(rnd)), (4, {"Platypodinae"}))
        self.assertTrue(all(i["check"] for i in rnd.items))

    def test_a_focus_with_only_unchecked_beetles_fills_the_batch_with_them(self):
        for _ in range(6):
            self.roi(self.t_plat, validated=False)
            self.roi(self.t_affinis)
        self.focus("Platypodinae")
        rnd = game.start_round(self.user, "classify", size=4)
        self.assertEqual((len(rnd.items), self.subfamilies(rnd)), (4, {"Platypodinae"}))
        self.assertFalse(any(i["check"] for i in rnd.items))

    @override_settings(GAME_ROUND_SIZE=2)
    def test_with_every_beetle_in_the_focus_seen_the_end_of_the_feed_offers_to_clear_it(self):
        for _ in range(2):
            self.roi(self.t_plat, validated=False)
            self.roi(self.t_affinis, validated=False)
        self.focus("Platypodinae", "identification", "choose_game")
        GamePreference.objects.filter(player=self.user).update(play_mode="classify")
        data = self.started()
        res = self.answer(data, AFFINIS)
        res = self.answer(dict(data, item=res["item"]), AFFINIS)
        self.assertTrue(res["done"])
        self.assertEqual(res["caught_up"], {"text": SEEN_FOCUS, "clear_focus": True})
        # clearing it (the screen's "Show everything") brings the rest
        self.assertEqual(self.post("game_prefs", {"focus_rank": ""}).status_code, 200)
        rnd = GameRound.objects.get(id=self.started(fresh=True)["round"])
        self.assertEqual(self.subfamilies(rnd), {"Scolytinae"})


class SeenEverythingTests(FallbackCase):
    @override_settings(GAME_ROUND_SIZE=2)
    def test_the_feed_ends_saying_every_beetle_has_been_seen(self):
        self.roi(self.t_affinis, validated=False)
        self.roi(self.t_ferr, validated=False)
        # Identification chosen; Similarity, the fallback, can't use unchecked beetles alone
        self.grant("identification", "choose_game", play_mode="classify")
        data = self.started()
        res = self.answer(data, AFFINIS)
        res = self.answer(dict(data, item=res["item"]), AFFINIS)
        self.assertTrue(res["done"])
        self.assertEqual(res["caught_up"], {"text": SEEN_ALL, "clear_focus": False})

    def test_a_new_feed_says_so_when_every_checked_beetle_was_shown(self):
        rnd = GameRound.objects.create(player=self.user, mode="classify", items=[])
        for i, taxon in enumerate((self.t_affinis, self.t_ferr)):
            GameAnswer.objects.create(round=rnd, player=self.user, mode="classify", index=i, roi=self.roi(taxon),
                                      is_check=True, **AFFINIS)
        res = self.start()
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["error"], SEEN_ALL)


class LockedGameTests(FallbackCase):
    def setUp(self):
        super().setUp()
        for taxon in (self.t_affinis, self.t_ferr, self.t_plat) * 3:
            self.roi(taxon)
            self.roi(taxon, validated=False)

    def test_a_chosen_game_above_the_players_level_plays_the_mix(self):
        self.grant("odd_one_out", "choose_game", play_mode="classify")   # Identification is level 4
        data = self.started()
        self.assertEqual(data["prefs"]["play_mode"], "both")
        modes = {i["mode"] for i in GameRound.objects.get(id=data["round"]).items}
        self.assertTrue(modes)
        self.assertNotIn("classify", modes)

    def test_a_new_player_with_a_grid_game_saved_plays_similarity(self):
        self.grant(play_mode="select")   # e.g. their level went down
        data = self.started()
        self.assertEqual((data["prefs"]["play_mode"], data["item"]["mode"], data["notice"]), ("pair", "pair", ""))


class PageTests(FallbackCase):
    def test_the_page_shows_the_notice_once_and_offers_to_clear_the_focus_when_caught_up(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_play", args=["mixed"])).content.decode()
        self.assertIn('data-testid="caughtup-focus"', page)
        self.assertIn("You&rsquo;ve seen every beetle we have. New photos are added regularly.", page)
        self.assertIn("if (data.notice && !noticesShown.has(data.notice))", page)
        self.assertIn('$("caughtup-focus").addEventListener("click", () => savePrefs({ focus_rank: "" }));', page)
        self.assertEqual(page.count("feedNotes(data);"), 2)   # the start of the feed, and every answer
