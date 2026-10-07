"""
#618 shared components, finished (site-upload, site-buttons, site-back, site-focus, site-targets, site-uuid):
every file input is the one drop zone, buttons are picked by role, sub-pages use the one back link, nothing hides the
site's focus ring, small links get a 44px tap target, and an id shown in passing is 8 characters and a copy button.
"""
import re

from django.conf import settings
from django.template import engines
from django.test import SimpleTestCase

ROOT = settings.BASE_DIR / "beetlesgallery"
TEMPLATES = ROOT / "templates"
INPUT_CSS = ROOT / "static" / "css" / "input.css"
JS = ROOT / "static" / "js"


def template(name):
    return (TEMPLATES / name).read_text(encoding="utf-8")


def render(name, context):
    return engines["django"].from_string("{% include '" + name + "' %}").render(context)


def opening_tag(html, marker):
    start = html.rfind("<", 0, html.index(marker))
    return html[start:html.index(">", start) + 1]


class UploadComponentTests(SimpleTestCase):
    """site-upload: one drop zone for every file input, with the type and size limit inside it."""

    def test_no_raw_file_input_is_left(self):
        found = []
        for path in TEMPLATES.rglob("*.html"):
            name = path.relative_to(TEMPLATES).as_posix()
            if name in ("beetles/includes/drop_zone.html", "beetles/tool_classify.html"):   # the component; IBBI-AI's own
                continue
            if re.search(r"""<input\b[^>]*type=["']file["']""", path.read_text(encoding="utf-8")):
                found.append(name)
        self.assertEqual(found, [])

    def test_the_old_csv_only_zone_is_gone(self):
        self.assertFalse((TEMPLATES / "beetles/includes/csv_dropzone.html").exists())

    def test_the_upload_pages_use_the_drop_zone(self):
        for name in ("beetles/upload_interactions.html", "beetles/upload_predictions.html",
                     "beetles/upload_interaction_proposals.html"):
            with self.subTest(name=name):
                source = template(name)
                self.assertIn('{% include "beetles/includes/drop_zone.html" with input_id="csv_file"', source)
                self.assertIn('type_label=".csv" max_mb=max_mb', source)

    def test_the_data_management_dialogs_use_the_drop_zone_and_keep_their_field_names(self):
        source = template("beetles/data_management.html")
        self.assertEqual(source.count('{% include "beetles/includes/drop_zone.html"'), 5)
        self.assertIn('input_id="new-csv-file" name="csv_file"', source)
        self.assertIn('input_id="new-zip-file" name="zip"', source)   # the chunked upload finds it by name
        self.assertIn('data-chunk-input="zip"', source)
        self.assertIn('input_id="update-csv-file" name="csv_file"', source)
        self.assertIn('input_id="predictions-file" name="csv_file"', source)
        self.assertIn('input_id="taxonomy-csv-file" name="csv_file"', source)

    def test_the_zone_carries_the_input_and_shows_type_and_limit(self):
        html = render("beetles/includes/drop_zone.html", {
            "input_id": "new-zip-file", "name": "zip", "label": "Images", "accept": ".zip", "required": True,
            "small": True, "type_label": ".zip", "max_mb": 10, "submit_id": "go",
        })
        self.assertIn('id="new-zip-file" name="zip" type="file"', html)
        self.assertIn('accept=".zip" required', html)
        self.assertIn(".zip, up to 10 MB", html)
        self.assertIn('data-submit="go"', html)
        self.assertIn('<label for="new-zip-file"', html)
        self.assertIn("data-drop-zone-filename", html)

    def test_the_script_shows_the_file_name_and_enables_the_button(self):
        script = (JS / "drop_zone.js").read_text(encoding="utf-8")
        self.assertIn("data-drop-zone-filename", script)
        self.assertIn("data-submit", script)
        self.assertIn("addEventListener('reset'", script)


class ButtonRoleTests(SimpleTestCase):
    """site-buttons: btn-main once per page, btn-primary to commit a form, btn-secondary for the rest."""

    def test_the_data_management_dialogs_commit_with_btn_primary(self):
        source = template("beetles/data_management.html")
        for button_id in ("btn-submit-new", "btn-submit-update", "btn-submit-predictions", "btn-submit-taxonomy"):
            with self.subTest(button=button_id):
                self.assertIn('class="btn-primary"', opening_tag(source, f'id="{button_id}"'))
        self.assertNotIn("border-red-300 bg-red-50 px-4 py-2", source)   # Cancel is not a danger

    def test_the_image_browser_has_one_main_button_search(self):
        source = template("beetles/image_browser.html")
        self.assertEqual(source.count("btn-main"), 1)
        self.assertIn("btn-main", opening_tag(source, ">Search</button>"))

    def test_one_main_button_on_the_game_pages(self):
        for name in ("beetles/game_home.html", "beetles/game_round_review.html"):
            with self.subTest(name=name):
                self.assertEqual(template(name).count("btn-main"), 1)

    def test_account_forms_use_the_house_buttons(self):
        self.assertIn("btn-primary", opening_tag(template("accounts/signin.html"), 'value="Sign in"'))
        self.assertIn("btn-secondary", opening_tag(template("accounts/signin.html"), 'data-testid="request-access"'))
        self.assertNotIn("inline-flex w-auto justify-center rounded-md", template("accounts/my_account.html"))


class BackLinkTests(SimpleTestCase):
    """site-back: a small "<- Parent page" link above the title, never a bare chevron beside it."""

    def test_no_bare_chevron_beside_a_title(self):
        for name in ("beetles/game_report.html", "beetles/game_round_review.html"):
            with self.subTest(name=name):
                source = template(name)
                self.assertNotIn("fi-rr-angle-left", source)
                self.assertIn('{% include "beetles/includes/back_link.html"', source)

    def test_every_back_link_is_the_shared_one(self):
        hand_made = re.compile(r'<a href="[^"]*" class="inline-block py-2 text-sm text-gray-500 hover:text-gray-800">&larr;')
        found = [p.relative_to(TEMPLATES).as_posix() for p in TEMPLATES.rglob("*.html")
                 if hand_made.search(p.read_text(encoding="utf-8"))]
        self.assertEqual(found, [])

    def test_the_link_reads_as_before_and_has_a_tap_target(self):
        html = render("beetles/includes/back_link.html", {"url": "/account/", "label": "Account"})
        self.assertIn('href="/account/"', html)
        self.assertIn("&larr; Account</a>", html)
        self.assertIn("hit-area", html)


class FocusRingTests(SimpleTestCase):
    """site-focus: the one 2px gray-600 ring (input.css :focus-visible); nothing hides or replaces it."""

    def test_the_ring_is_defined_once(self):
        css = INPUT_CSS.read_text(encoding="utf-8")
        self.assertRegex(css, r":focus-visible \{\s*outline: 2px solid var\(--color-gray-600\);\s*outline-offset: 2px;")

    def test_no_template_or_form_hides_or_replaces_it(self):
        sources = {p.relative_to(TEMPLATES).as_posix(): p.read_text(encoding="utf-8") for p in TEMPLATES.rglob("*.html")}
        sources["forms.py"] = (ROOT / "beetles_app" / "forms.py").read_text(encoding="utf-8")
        bad = re.compile(r"(?<![\w:/\[-])(?:outline-hidden|outline-none|focus:ring[\w./-]*|focus-visible:ring[\w./-]*"
                         r"|focus-visible:outline[\w./-]*|focus:outline[\w./-]*|focus:border-primary)(?![\w-])")
        found = {name: sorted(set(bad.findall(text))) for name, text in sources.items() if bad.search(text)}
        self.assertEqual(found, {})


class TapTargetTests(SimpleTestCase):
    """site-targets: a 44x44px hit area on touch for every small link or icon button."""

    def test_the_helper_is_in_the_theme(self):
        css = INPUT_CSS.read_text(encoding="utf-8")
        self.assertIn(".hit-area { position: relative; }", css)
        rule = css[css.index("@media (pointer: coarse)"):]
        self.assertIn("width: max(100%, 44px);", rule)
        self.assertIn("height: max(100%, 44px);", rule)

    def test_the_small_links_have_it(self):
        game_home = template("beetles/game_home.html")
        self.assertIn("hit-area", opening_tag(game_home, ">How it works</a>"))
        self.assertEqual(game_home.count('hover:text-gray-900 hit-area">All</a>'), 2)
        self.assertIn("hit-area", opening_tag(template("beetles/bulk_validate.html"), ">Open</a>"))
        data = template("beetles/data_management.html")
        self.assertNotIn('hover:no-underline">', data)   # every Details button, also those the script writes
        self.assertIn("hit-area", template("beetles/includes/short_id.html"))

    def test_the_roi_buttons_are_44px(self):
        detail = template("beetles/detail.html")
        self.assertNotIn("flex h-10 w-10", detail)
        self.assertEqual(detail.count("flex h-11 w-11"), 6)   # the photo toolbar: ROI pager, boxes, full size
        # the small pager in the header keeps its look and gets the 44px tap target
        self.assertIn("hit-area", opening_tag(detail, 'title="Next ROI" aria-label="Next ROI">'))


class ShortIdTests(SimpleTestCase):
    """site-uuid: an id in passing is 8 characters in mono and a copy button (includes/short_id.html)."""

    def test_the_gallery_cards_show_the_short_id(self):
        source = template("beetles/includes/gallery_results.html")
        self.assertIn('{% include "beetles/includes/short_id.html" with value=b.id %}', source)
        self.assertNotIn('{{ b.id|stringformat:"s" }}</span>', source)
        self.assertNotIn("copy-uuid", template("beetles/image_browser.html"))

    def test_the_data_management_tables_show_the_short_id(self):
        source = template("beetles/data_management.html")
        for value in ("job.id", "b.id", "u.id"):
            self.assertIn('{% include "beetles/includes/short_id.html" with value=' + value + " %}", source)
        self.assertNotIn("copy-uuid", source)

    def test_the_specimen_identifiers_block_keeps_full_values(self):
        # skipped by the owner (detail-ids-copy): the Identifiers block still shows each id in full
        detail = template("beetles/detail.html")
        self.assertIn('data-testid="roi-id-value">{{ beetle.id }}</span>', detail)
