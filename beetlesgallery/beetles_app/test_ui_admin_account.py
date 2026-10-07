"""
UI audit (#618): Bulk validation, Access requests, Site notice, Account management and Create user.

* Bulk validation: a sticky bottom bar ("n selected · Validate") instead of one that scrolls away; the whole card
  is the toggle, with its checkbox in the caption row rather than drawn over the specimen; the caption says the
  species, the box count and whether every box is named; filters apply themselves on change, with no Filter button.
* Access requests: the permission boxes are grouped (Viewing / Editing / Admin) instead of one long list, what was
  asked for gets a "Requested" pill and sorts first; "Grant selected" / "Deny" with a short helper line; a short
  "Note (optional)" label.
* Site notice: a live preview in the banner's own style; the back link says "Account" everywhere (not "My account").
* Account management: one settings list instead of a card per action; a header saying whose account it is; the
  waiting-request pill never wraps and shows only the number; the user directory is cards on mobile.
* Create user: "Create user" is the title, the button and the Account page link (no longer three different names);
  the button is .btn-primary (no black border); the explanation is the page intro, above the form; a back link to
  Account; the grey focus ring used everywhere else (not an orange/amber one).
"""
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from beetlesgallery.beetles_app import access, areas
from beetlesgallery.beetles_app.forms import TAILWIND_INPUT
from beetlesgallery.beetles_app.models import AccessRequest, AreaGrant
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_image, make_taxon

PASSWORD = "Correct-Horse-9-Staple"


class AreaGroupingTests(PageBehaviourCase):
    """areas.grouped_areas (acc-groups): every area in exactly one of Viewing / Editing / Admin, requested first."""

    def test_every_area_is_in_exactly_one_group(self):
        grouped = areas.grouped_areas()
        keys = [a["key"] for g in grouped for a in g["areas"]]
        self.assertEqual(sorted(keys), sorted(areas.KEYS))
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual([g["group"] for g in grouped], ["Viewing", "Editing", "Admin"])

    def test_requested_areas_are_ticked_and_sort_first_in_their_group(self):
        grouped = access.grouped({areas.UPDATE, areas.UPLOAD})
        editing = next(g for g in grouped if g["group"] == "Editing")
        wanted = [a["key"] for a in editing["areas"] if a["wanted"]]
        self.assertEqual(set(wanted), {areas.UPDATE, areas.UPLOAD})
        # every wanted area in the group comes before every area that was not asked for
        flags = [a["wanted"] for a in editing["areas"]]
        self.assertEqual(flags, sorted(flags, reverse=True))


class SiteNoticePreviewTests(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        AreaGrant.objects.get_or_create(user=self.staff, area=areas.NOTICE)
        self.client.force_login(self.staff)

    def test_the_preview_shows_the_current_message_in_the_banner_style(self):
        self.client.post(reverse("site_notice"), {"text": "Testing today", "active": "on"})
        page = self.client.get(reverse("site_notice"))
        self.assertContains(page, 'data-testid="notice-preview"')
        self.assertContains(page, "Testing today")
        self.assertContains(page, "bg-gray-800")   # the real banner's own colour (base.html)

    def test_the_back_link_says_account(self):
        page = self.client.get(reverse("site_notice"))
        self.assertContains(page, '&larr; Account</a>')
        self.assertNotContains(page, "My account")
        self.assertNotContains(page, "Account Management")


class AccountPageNamingAndLayoutTests(PageBehaviourCase):
    def test_the_page_and_the_menu_both_say_account(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("my_account"))
        self.assertContains(page, '<h1 class="page-title">Account</h1>')
        self.assertContains(page, ">Account<")   # the sidebar/menu link text
        self.assertNotContains(page, "Account Management")

    def test_the_settings_list_replaces_the_separate_cards(self):
        self.client.force_login(self.superuser)
        page = self.client.get(reverse("my_account"))
        self.assertContains(page, 'data-testid="account-settings"')
        self.assertNotContains(page, "Account Actions")

    def test_the_user_directory_has_both_a_mobile_card_list_and_a_desktop_table(self):
        self.client.force_login(self.superuser)
        page = self.client.get(reverse("my_account")).content.decode()
        self.assertIn('data-testid="user-card"', page)
        self.assertIn('id="user-directory"', page)
        self.assertIn("sm:hidden", page)
        self.assertIn("hidden sm:block", page)

    def test_the_users_settings_row_links_to_the_directory(self):
        self.client.force_login(self.superuser)
        self.assertContains(self.client.get(reverse("my_account")), 'href="#user-directory"')


class CreateUserNamingTests(PageBehaviourCase):
    """cre-names: "Create user" is the title, the button, and the Account page link - not three different names."""

    def test_the_dedicated_page_title_and_button_both_say_create_user(self):
        self.client.force_login(self.superuser)
        page = self.client.get(reverse("create_account"))
        self.assertContains(page, '<h1 class="page-title">Create user</h1>')
        self.assertContains(page, 'value="Create user"')

    def test_the_account_page_link_says_create_user_too(self):
        self.client.force_login(self.superuser)
        self.assertContains(self.client.get(reverse("my_account")), ">Create user<")

    def test_the_button_is_btn_primary_with_no_black_border(self):
        self.client.force_login(self.superuser)
        page = self.client.get(reverse("create_account")).content.decode()
        self.assertIn("btn-primary", page)
        self.assertNotIn("border-gray-900", page)
        self.assertNotIn("border-black", page)

    def test_the_intro_is_above_the_form_not_after_the_button(self):
        self.client.force_login(self.superuser)
        page = self.client.get(reverse("create_account")).content.decode()
        intro = page.index("Use this form to create new accounts")
        form = page.index('data-testid="create-account-form"')
        submit = page.index('value="Create user"')
        self.assertLess(intro, form)     # the explanation comes before the form, not after the button
        self.assertLess(form, submit)

    def test_the_page_has_a_back_link_to_account(self):
        self.client.force_login(self.superuser)
        self.assertContains(self.client.get(reverse("create_account")), '&larr; Account</a>')

    def test_the_focus_ring_is_grey_everywhere_the_shared_input_is_used(self):
        classes = TAILWIND_INPUT.split()
        # the site-wide :focus-visible ring (site-focus, #618) is the only one: no ring or outline class of its own
        self.assertFalse([c for c in classes if "ring" in c or "outline" in c], classes)
        for gone in ("focus:ring-amber-500", "focus:ring-orange-500", "focus:border-amber-500", "focus:border-orange-500"):
            self.assertNotIn(gone, classes)


class BulkValidateCardTests(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        self.checker = get_user_model().objects.create_user("bv-checker", password="pw")
        AreaGrant.objects.create(user=self.checker, area=areas.BULK_VALIDATE)
        self.client.force_login(self.checker)
        self.taxon = make_taxon(valid_species_id="bv-1", scientific_name="Xyleborinus saxesenii")
        self.image = make_image()
        make_beetle(image=self.image, taxon=self.taxon, bbox="unvalidated")
        make_beetle(image=self.image, taxon=self.taxon, bbox="unvalidated")

    def page(self):
        return self.client.get(reverse("bulk_validate")).content.decode()

    def test_the_caption_names_the_species_the_box_count_and_that_every_box_is_named(self):
        page = self.page()
        self.assertIn('data-testid="bulk-caption"', page)
        self.assertIn("Xyleborinus saxesenii", page)
        self.assertIn("2 boxes, all named", page)

    def test_an_unnamed_box_is_called_out(self):
        make_beetle(image=make_image(), bbox="unvalidated")   # no taxon
        page = self.page()
        self.assertIn("1 box, 1 unnamed", page)

    def test_the_checkbox_is_in_the_caption_row_not_drawn_over_the_photo(self):
        page = self.page()
        caption_at = page.index('data-testid="bulk-caption"')
        checkbox_at = page.index('class="bulk-pick')
        self.assertGreater(checkbox_at, caption_at)   # the checkbox comes after the caption, not over the image
        self.assertNotIn('class="bulk-pick absolute', page)   # no longer pinned over the specimen

    def test_the_card_itself_is_the_toggle_with_a_dark_border_when_selected(self):
        page = self.page()
        self.assertIn("has-checked:border-gray-700", page)

    def test_the_validate_control_is_a_sticky_bottom_bar_not_a_top_one(self):
        page = self.page()
        self.assertIn('data-testid="bulk-sticky-bar"', page)
        self.assertIn("sticky bottom-", page)
        self.assertNotIn("sticky top-0", page)
        self.assertIn("selected</span>", page)
        self.assertIn(">Validate<", page)

    def test_filters_apply_on_change_with_no_filter_button(self):
        page = self.page()
        self.assertIn("onchange=\"this.form.submit()\"", page)
        self.assertNotIn(">Filter<", page)


class AccessRequestsNoteAndButtonsTests(PageBehaviourCase):
    """acc-note, acc-buttons: a short label and helper text, "Grant selected" / "Deny"."""

    def setUp(self):
        super().setUp()
        self.applicant = get_user_model().objects.create_user("applicant", password="pw", is_active=False)
        AccessRequest.objects.create(
            name="Ada", email="ada@example.org", user=self.applicant, areas=["details"],
            email_verified_at=timezone.now(),
        )
        self.client.force_login(self.superuser)

    def test_the_note_field_has_a_short_label_and_a_helper_line(self):
        page = self.client.get(reverse("access_requests"))
        self.assertContains(page, "Note <span")
        self.assertContains(page, "Included in the email.")
        self.assertNotContains(page, "NOTE TO THEM")
        self.assertNotContains(page, "included in the email)")

    def test_the_buttons_say_grant_selected_and_deny_with_a_helper_line(self):
        page = self.client.get(reverse("access_requests"))
        self.assertContains(page, "Grant selected")
        self.assertContains(page, "Deny</button>")
        self.assertContains(page, "They stay on Basic.")
        self.assertNotContains(page, "Save: grant the ticked areas")
        self.assertNotContains(page, "Deny (stays Basic)")
