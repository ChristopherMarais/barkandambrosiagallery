"""
The UI audit's (#618) remaining page fixes: one number one colour for accuracy (site-meaning-85), name and authority
first on a specimen (detail-ids-first), the flag in the photo's toolbar (detail-toolbar), a one-sentence Data
management intro with the Image Browser link (data-actions, data-browse), grey boxes in the annotation tool
(ann-blue), grey role pills (acct-roles), the tick on the play header's daily chip (gh-today), a 14px Focus button
(play-modes), a short scoring section for players (how-dup) and the beetle mark in the admin (adm-theme).
"""
import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.urls import reverse

from beetlesgallery.beetles_app import game_board, game_scale
from beetlesgallery.beetles_app.models import PlayerScore
from beetlesgallery.beetles_app.test_details_photo_controls import Outline
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_image, make_taxon

TEMPLATES = Path(settings.BASE_DIR) / "beetlesgallery" / "templates"


class AccuracyOneColourTests(PageBehaviourCase):
    """site-meaning-85: the accuracy takes its own step on the scale; the comparison is a grey rank."""

    def test_the_step_is_the_values_own_not_the_percentile(self):
        User = get_user_model()
        for i, acc in enumerate([0.95, 0.96, 0.97, 0.98]):
            PlayerScore.objects.create(player=User.objects.create_user(f"p{i}"), accuracy=acc, judged=20)
        PlayerScore.objects.create(player=self.user, accuracy=0.4, judged=20)
        me = game_board.accuracy_standing(self.user)["me"]
        self.assertEqual(me["percentile"], 0)
        self.assertEqual(me["step"], game_scale.value_step(0.4))   # "decent", the same as everywhere else
        self.assertEqual(me["rank"], "Top 100%")
        self.assertNotIn("tier", me)

    def test_the_chip_is_a_grey_rank_and_the_number_has_the_colour(self):
        standing = {"players": 3, "bins": [], "average": 0.6,
                    "me": {"accuracy": 0.85, "percentile": 29, "rank": "Top 71%", "step": "excellent", "bin": 8}}
        html = render_to_string("beetles/includes/game_accuracy.html", {"standing": standing})
        chip = re.search(r'<span[^>]*data-testid="accuracy-rank"[^>]*>', html).group(0)
        self.assertIn("bg-gray-100", chip)
        self.assertNotIn("scale-", chip)
        self.assertIn('data-testid="accuracy-rank">Top 71%<', html)
        value = re.search(r'<span[^>]*data-testid="accuracy-value"[^>]*>', html).group(0)
        self.assertIn("scale-excellent", value)
        self.assertNotIn("scale-chip-", html)
        self.assertNotIn("uppercase", html)   # no DECENT-style word chip any more


class DetailHeaderAndToolbarTests(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        self.taxon = make_taxon(scientific_name="Ips typographus", authority="Linnaeus")
        self.roi = make_beetle(image=make_image(image_file="tests/photo.jpg"), bbox="unvalidated", taxon=self.taxon)

    def page(self):
        self.client.force_login(self.user)
        return self.client.get(reverse("beetle_detail", args=[self.roi.id])).content.decode()

    def test_the_title_shows_the_name_and_a_smaller_grey_authority(self):
        page = self.page()
        start = page.index('<h1 class="page-title">')
        h1 = page[start:page.index("</h1>", start)]
        self.assertIn("Ips typographus", h1)
        authority = re.search(r'<span[^>]*data-testid="detail-authority"[^>]*>([^<]*)<', h1)
        self.assertEqual(authority.group(1), "Linnaeus")
        self.assertIn("text-gray-500", authority.group(0))
        self.assertIn("font-normal", authority.group(0))

    def test_no_authority_without_a_name(self):
        bare = make_beetle(image=make_image(image_file="tests/photo.jpg"), bbox="unvalidated")
        self.client.force_login(self.user)
        page = self.client.get(reverse("beetle_detail", args=[bare.id])).content.decode()
        self.assertNotIn('data-testid="detail-authority"', page)

    def test_the_flag_sits_in_the_toolbar_right_beside_full_size(self):
        page = self.page()
        elements = Outline(page).elements
        self.assertIn("roi-toolbar", elements["report-roi-btn"]["inside"])
        self.assertNotIn("roi-photo", elements["report-roi-btn"]["inside"])
        self.assertNotIn("absolute", elements["report-roi-btn"]["class"].split())
        self.assertLess(page.index('data-testid="roi-fullsize"'), page.index('id="report-roi-btn"'))
        # its behaviour is kept: the menu, the post and hiding with the box
        self.assertEqual(elements["report-roi-menu"]["data-url"], reverse("report_roi", args=[self.roi.id]))
        self.assertIn("flag.addEventListener('click'", page)
        self.assertIn('#roi-photo[data-box="hidden"]) #report-roi-wrap { display: none; }', page)


class DataManagementIntroTests(PageBehaviourCase):
    def test_the_intro_is_one_sentence_linking_the_image_browser(self):
        self.client.force_login(self.superuser)
        page = self.client.get(reverse("data_management")).content.decode()
        start = page.index('data-testid="data-actions-intro"')
        intro = page[start:page.index("</p>", start)]
        words = re.sub(r"<[^>]+>", "", intro.split(">", 1)[1])
        self.assertIn("To download images, use the Image Browser.", " ".join(words.split()))
        self.assertIn(f'<a href="{reverse("beetles_image_browser")}"', intro)
        for gone in ("Upload New Data</strong>", "Update Metadata</strong>"):
            self.assertNotIn(gone, intro)   # the rows below say what each action does
        self.assertNotIn("Browse gallery to download", page)


class AnnotationBoxColourTests(PageBehaviourCase):
    def test_boxes_are_gray_900_and_their_line_shows_validation(self):
        source = (TEMPLATES / "beetles" / "tool_annotate.html").read_text(encoding="utf-8")
        fn = source[source.index("function drawCanvas"):source.index("function fitToScreen")]
        self.assertIn("const BOX_INK = '#111827';", fn)
        self.assertIn("const boxColor = BOX_INK;", fn)
        for gone in ("#10b981", "#f59e0b", "#3b82f6"):
            self.assertNotIn(gone, fn)
        self.assertIn("ctx.setLineDash(validated ? [] : [6/state.zoom, 4/state.zoom]);", fn)
        self.assertIn('data-testid="box-key">Solid box: validated &middot; dashed: not yet<', source)


class RolePillTests(PageBehaviourCase):
    def pills(self):
        self.client.force_login(self.superuser)
        html = self.client.get(reverse("my_account")).content.decode()
        return {word: re.findall(rf'<span class="([^"]*rounded-full[^"]*)">{word}</span>', html)
                for word in ("Superuser", "Staff", "Standard")}

    def test_superuser_is_gray_700_and_the_others_gray_100_with_dark_text(self):
        pills = self.pills()
        for word, classes in pills.items():
            self.assertTrue(classes, word)
            for c in classes:
                with self.subTest(word=word, classes=c):
                    if word == "Superuser":
                        self.assertIn("bg-gray-700", c.split())
                        self.assertIn("text-white", c.split())
                    else:
                        self.assertIn("bg-gray-100", c.split())
                        self.assertIn("text-gray-800", c.split())
                    self.assertNotIn("bg-black", c)


class PlayHeaderTests(PageBehaviourCase):
    def page(self):
        self.client.force_login(self.user)
        return self.client.get(reverse("game_play", args=["mixed"])).content.decode()

    def test_the_daily_chip_ticks_when_the_goal_is_met(self):
        page = self.page()
        self.assertIn('id="chip-check"', page)
        self.assertIn('$("chip-check").classList.toggle("hidden", !lit);', page)

    def test_focus_is_14px_with_a_44px_target(self):
        page = self.page()
        button = re.search(r'<button[^>]*id="focus-btn"[^>]*>', page).group(0)
        self.assertIn("text-sm", button)
        self.assertIn("h-11", button)
        self.assertNotIn("text-xs", button)


class HowItWorksShortTests(PageBehaviourCase):
    def page(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("game_how")).content.decode()

    def test_players_get_no_point_tables(self):
        page = self.page(self.user)
        known = page[page.index('data-testid="how-known"'):page.index('id="unchecked"')]
        self.assertNotIn("<table", known)
        self.assertNotIn("&times;", known)
        for game in ("Naming", "Similarity", "Odd One Out", "Find Them All"):
            self.assertIn(game, known)
        self.assertNotIn('data-testid="how-scoring-link"', page)

    def test_superusers_keep_the_link_to_the_full_numbers(self):
        page = self.page(self.superuser)
        self.assertIn('data-testid="how-scoring-link"', page)
        self.assertIn(f'href="{reverse("game_scoring")}"', page)


class AdminBrandingTests(PageBehaviourCase):
    def test_the_admin_header_has_the_beetle_mark_and_stays_grey(self):
        self.client.force_login(self.superuser)
        page = self.client.get(reverse("admin:index")).content.decode()
        self.assertIn('data-testid="admin-beetle-mark"', page)
        self.assertIn("img/IMG_5557", page)
        self.assertIn("#header { background: #1f2937;", page)
