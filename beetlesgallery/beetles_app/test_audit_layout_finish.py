"""
The UI audit's layout items, finished (issue #618):
  - site-tables: every wide table has a phone layout: priority columns with the rest on tap, rows as cards
    (.table-cards), or as the last resort a scroll box whose cut edge fades (.table-scroll); none is left as a
    plain overflow-x-auto box
  - site-long: How it works, Scoring, the Pathogen dataset's About tab and the round review open with a summary,
    fold their parts into <details>, and keep a sticky contents bar (chips) with a "Jump to" select on a phone
    (static/js/jump_to.js, which opens the folded part it jumps to)
  - site-progress: progress bars are gray-700 on gray-200 (.progress-bar), scale colours only for a rating
  - site-icons: the game is the play icon everywhere, and icons line up with flex, not margin nudges
  - nav-active: every page below a menu section marks that section, including pages outside its URL prefix
"""
import re
from pathlib import Path
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.urls import reverse

from beetlesgallery.beetles_app.templatetags.beetle_tags import nav_active, nav_home_active, nav_section_title
from beetlesgallery.beetles_app.testing import PageBehaviourCase

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = REPO / "beetlesgallery" / "templates"
STATIC = REPO / "beetlesgallery" / "static"
INPUT_CSS = (STATIC / "css" / "input.css").read_text(encoding="utf-8")
JUMP_TO_JS = (STATIC / "js" / "jump_to.js").read_text(encoding="utf-8")
TABLES_JS = (STATIC / "js" / "tables.js").read_text(encoding="utf-8")
ACTIVE = "bg-gray-200 font-semibold"


def template(name):
    return (TEMPLATES / name).read_text(encoding="utf-8")


def site_templates():
    for path in sorted(TEMPLATES.rglob("*.html")):
        if "emails" in path.parts or "admin" in path.parts:
            continue
        yield path


class SiteTablesTests(SimpleTestCase):
    def test_the_shared_card_and_scroll_layouts_exist(self):
        self.assertIn(".table-cards", INPUT_CSS)
        self.assertIn("content: attr(data-label);", INPUT_CSS)
        self.assertIn(".table-scroll {", INPUT_CSS)
        self.assertIn(".table-scroll.is-cut {", INPUT_CSS)
        self.assertIn("mask-image", INPUT_CSS)
        self.assertIn("window.labelTableCards = labelTableCards;", TABLES_JS)
        self.assertIn("classList.toggle('is-cut'", TABLES_JS)
        self.assertIn("js/tables.js", template("base.html"))

    def test_no_table_is_left_in_a_plain_sideways_scroll_box(self):
        plain = re.compile(r'<div class="[^"]*\boverflow-x-auto\b[^"]*">\s*<table')
        offenders = [p.name for p in site_templates() if plain.search(p.read_text(encoding="utf-8"))]
        self.assertEqual(offenders, [])

    def test_the_data_management_tables_turn_into_cards(self):
        page = template("beetles/data_management.html")
        # history, downloads, uploads, updates and the field definitions (the column guide)
        self.assertEqual(page.count('<table class="table-cards'), 5)
        self.assertIn("@media (min-width: 640px) { .wide-table > table { min-width: 36rem; } }", page)
        # rows added by the script get their column names too
        self.assertIn("window.labelTableCards(tbody.closest('table'))", page)

    def test_the_scoring_tables_turn_into_cards(self):
        page = template("beetles/game_scoring.html")
        for testid in ("scoring-thresholds", "scoring-play", "difficulty-examples", "scoring-examples"):
            with self.subTest(table=testid):
                tag = re.search(r'<table class="([^"]*)" data-testid="%s"' % testid, page)
                self.assertIsNotNone(tag)
                self.assertIn("table-cards", tag.group(1))
        self.assertEqual(page.count('<table class="table-cards w-full">'), 2)   # what a beetle is, and is not

    def test_other_wide_tables_have_a_phone_layout(self):
        self.assertIn('<table class="table-cards w-full text-sm border', template("privacy.html"))
        self.assertIn('<table class="table-cards w-full text-sm">', template("beetles/access_requests.html"))
        self.assertIn('<div class="table-scroll border', template("beetles/game_settings.html"))


class SiteLongTests(SimpleTestCase):
    def test_jump_to_opens_the_folded_part_it_jumps_to(self):
        self.assertIn("el.tagName === 'DETAILS'", JUMP_TO_JS)
        self.assertIn("el.open = true", JUMP_TO_JS)
        self.assertIn("window.jumpTo = jump;", JUMP_TO_JS)
        self.assertIn("hashchange", JUMP_TO_JS)
        self.assertIn("js/jump_to.js", template("base.html"))

    def test_how_it_works_folds_its_parts_under_a_contents_bar(self):
        page = template("beetles/game_how.html")
        self.assertIn('data-testid="quick-start"', page)   # the summary
        self.assertIn('data-testid="how-toc"', page)
        self.assertIn("sticky top-0", page)
        self.assertIn('aria-label="Jump to section" data-jump-to>', page)
        self.assertGreaterEqual(page.count('<details class="group">'), 8)
        for anchor in ("scoring", "known", "unchecked", "rescoring", "mistakes", "not-sure", "levels", "labels",
                       "leaderboard", "faq"):
            with self.subTest(anchor=anchor):
                self.assertIn(f'id="{anchor}"', page)
                self.assertIn(f'<option value="{anchor}">', page)

    def test_scoring_folds_its_sections_and_uses_the_shared_jump_to(self):
        page = template("beetles/game_scoring.html")
        self.assertIn('data-testid="scoring-summary"', page)
        self.assertEqual(page.count("<details class=\"group border-b border-gray-200 pb-4\""), 12)
        self.assertIn('aria-label="Jump to section" data-jump-to>', page)
        self.assertNotIn("location.hash = this.value", page)
        self.assertIn('<option value="changes">Changes</option>', page)

    def test_the_pathogen_dataset_about_tab_folds_its_parts(self):
        page = template("beetles/interactions_preview.html")
        self.assertIn('data-testid="about-toc"', page)
        self.assertIn("data-jump-to", page)
        for part in ("about-scope", "about-fig1", "about-fig2", "about-fig3", "about-evidence-section", "about-cite"):
            with self.subTest(part=part):
                self.assertIn(f'<details id="{part}"', page)
                self.assertIn(f'<option value="{part}">', page)
        self.assertIn('window.jumpTo("about-evidence-section", true)', page)

    def test_a_long_round_review_has_a_summary_contents_and_folds_the_correct_answers(self):
        page = template("beetles/game_round_review.html")
        self.assertIn('data-testid="review-summary"', page)
        self.assertIn('data-testid="review-toc"', page)
        self.assertIn('aria-label="Jump to answer" data-jump-to>', page)
        self.assertIn('<details id="correct-group"', page)
        self.assertIn('correctList.appendChild(li);', page)
        self.assertIn("li.id = `answer-${n + 1}`;", page)
        # what it already did is kept: the long press, the boxed whole photo, the one-line correct answers
        self.assertIn('window.onLongPress($("items"), ".review-photo",', page)
        self.assertIn('{% include "beetles/includes/roi_box_css.html" %}', page)
        self.assertIn('li.dataset.testid = "item-row-correct"', page)


class SiteProgressTests(SimpleTestCase):
    def test_no_progress_bar_is_green_or_its_own_grey(self):
        for path in site_templates():
            with self.subTest(template=path.name):
                self.assertNotIn("h-full bg-gray-600 rounded-full", path.read_text(encoding="utf-8"))

    def test_the_dataset_validation_bar_is_neutral(self):
        page = template("beetles/interactions_preview.html")
        rule = page[page.index(".bar-validation {"):]
        rule = rule[:rule.index("}")]
        self.assertIn("#374151", rule)
        self.assertNotIn("#16a34a", rule)

    def test_the_shared_progress_bar_classes_are_used(self):
        for name in ("beetles/data_management.html", "beetles/game_home.html", "beetles/game_unlocks.html",
                     "beetles/tool_classify.html", "beetles/game_report.html"):
            with self.subTest(template=name):
                page = template(name)
                self.assertIn('class="progress-bar', page)
                self.assertIn("progress-bar-fill", page)

    def test_a_rating_bar_keeps_the_scale(self):
        page = template("beetles/game_report.html")
        self.assertIn("scale-fill-{{ m.accuracy|scale }}", page)
        self.assertIn("scale-fill-{{ report.challenge|scale }}", page)

    def test_the_level_bar_runs_on_gray_200(self):
        self.assertIn('<div class="h-1 bg-gray-200"><div id="level-bar" class="h-full bg-gray-700',
                      template("beetles/game_play.html"))


class SiteIconsTests(SimpleTestCase):
    def test_the_game_is_the_play_icon_everywhere(self):
        for path in site_templates():
            with self.subTest(template=path.name):
                self.assertNotIn("fi-rr-gamepad", path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(template("beetles/tool_annotate.html").count("fi-rr-play"), 3)

    def test_icons_are_not_nudged_with_margins(self):
        nudge = re.compile(r'<i class="fi [^"]*\bmt-[0-9.]+')
        offenders = [p.name for p in site_templates() if nudge.search(p.read_text(encoding="utf-8"))]
        self.assertEqual(offenders, [])
        self.assertIn('<span class="flex h-5 shrink-0 items-center"><i class="fi {% if ok %}',
                      template("beetles/game_scoring.html"))


def ctx(path, signed_in=True):
    user = SimpleNamespace(is_authenticated=signed_in)
    return {"request": SimpleNamespace(path=path, user=user), "user": user, "game_name": "Ambrosia Archive"}


class NavActiveTagTests(SimpleTestCase):
    def test_pages_outside_a_section_prefix_mark_their_parent(self):
        cases = {
            "/tools/predictions/": "/my-uploads/",
            "/tools/predictions/0b0b0b0b-0000-0000-0000-000000000000/": "/my-uploads/",
            "/tools/bulk-validate/": "/my-uploads/",
            "/tools/site-notice/": "/accounts/me/",
            "/tools/access-requests/": "/accounts/me/",
            "/accounts/create-account/": "/accounts/me/",
            "/game/leaderboard/": "/game/",
            "/game/rounds/0b0b0b0b-0000-0000-0000-000000000000/": "/game/",
            "/game/scoring/": "/game/",
            "/interactions/review/": "/interactions/",
            "/interactions/upload/": "/interactions/",
            "/taxonomy/search/": "/taxonomy/",
        }
        for path, parent in cases.items():
            with self.subTest(path=path):
                self.assertEqual(nav_active(ctx(path), parent), ACTIVE)

    def test_other_sections_stay_unmarked(self):
        self.assertEqual(nav_active(ctx("/tools/predictions/"), "/tools/annotate/"), "hover:bg-gray-200")
        self.assertEqual(nav_active(ctx("/tools/site-notice/"), "/my-uploads/"), "hover:bg-gray-200")
        self.assertEqual(nav_active(ctx("/tools/classify/"), "/my-uploads/"), "hover:bg-gray-200")

    def test_home_marks_itself_and_the_team_page(self):
        self.assertEqual(nav_home_active(ctx("/")), ACTIVE)
        self.assertEqual(nav_home_active(ctx("/team/")), ACTIVE)
        self.assertEqual(nav_home_active(ctx("/beetles/")), "hover:bg-gray-200")

    def test_the_mobile_title_follows_the_same_sections(self):
        self.assertEqual(nav_section_title(ctx("/tools/bulk-validate/")), "Data Management")
        self.assertEqual(nav_section_title(ctx("/tools/access-requests/")), "Account & settings")
        self.assertEqual(nav_section_title(ctx("/accounts/signup/", signed_in=False)), "Login")
        self.assertEqual(nav_section_title(ctx("/accounts/signup/")), "Account & settings")


class NavActivePageTests(PageBehaviourCase):
    def nav_link_classes(self, page, url):
        start = page.index(f'href="{url}"')
        return page[start:page.index(">", start)]

    def test_model_predictions_marks_data_management(self):
        self.client.force_login(self.superuser)
        page = self.client.get(reverse("upload_predictions")).content.decode()
        self.assertIn(ACTIVE, self.nav_link_classes(page, reverse("data_management")))

    def test_the_site_notice_marks_account(self):
        self.client.force_login(self.superuser)
        page = self.client.get(reverse("site_notice")).content.decode()
        self.assertIn(ACTIVE, self.nav_link_classes(page, reverse("my_account")))
