"""
The owner's phone review, round 6: on a specimen's page the other images of the same specimen come last, under
every detail card; Log out sits under Account in the phone menu and the desktop rail; IBBI-AI's model picker is at
the top of its page, before the drop zone; and the funders' logos on the home page wrap instead of running off a
phone's edge.
"""
import re

from django.urls import reverse

from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_image, make_taxon


class OtherImagesComeLastTests(PageBehaviourCase):
    def test_the_other_images_are_under_every_detail_card(self):
        taxon = make_taxon(scientific_name="Ips typographus")
        roi = make_beetle(image=make_image(image_file="tests/photo.jpg"), taxon=taxon, depicts_specimen="SP-6")
        make_beetle(image=make_image(image_file="tests/photo2.jpg"), taxon=taxon, depicts_specimen="SP-6")
        self.client.force_login(self.user)
        page = self.client.get(reverse("beetle_detail", args=[roi.id])).content.decode()
        related = page.index('data-testid="related-specimens"')
        self.assertIn("Other images of this specimen (1)", page[related:])
        for card in (">Taxonomy<", ">Specimen and collection<", ">Attribution<", ">Validation<", ">Identifiers<"):
            self.assertLess(page.index(card), related, card)
        self.assertLess(page.index("</aside>"), related)   # out of the photo's column, after the details
        self.assertNotIn("copy-specimen-id", page)   # detail-ids-copy stays skipped (#618)


class LogOutUnderAccountTests(PageBehaviourCase):
    def page(self):
        self.client.force_login(self.user)
        return self.client.get(reverse("beetles_image_browser")).content.decode()

    def test_the_phone_menu_has_account_then_log_out(self):
        page = self.page()
        drawer = page[page.index('id="mobile-menu"'):page.index('id="sidenav"')]
        account = drawer.index(f'href="{reverse("my_account")}"')
        logout = drawer.index(f'action="{reverse("logout")}"')
        self.assertLess(account, logout)
        self.assertEqual(drawer.count(f'action="{reverse("logout")}"'), 1)
        self.assertLess(drawer.index('id="mobile-menu-footer"'), account)

    def test_the_desktop_rail_has_account_then_log_out(self):
        page = self.page()
        rail = page[page.index('<aside id="sidenav"'):]
        rail = rail[:rail.index("</aside>")]
        account = rail.index(f'href="{reverse("my_account")}"')
        logout = rail.index(f'action="{reverse("logout")}"')
        self.assertLess(account, logout)
        self.assertEqual(rail.count(f'action="{reverse("logout")}"'), 1)
        self.assertLess(rail.index('id="sidenav-footer"'), account)

    def test_the_log_out_row_is_padded_like_the_rails_other_rows(self):
        css = " ".join(self.page().split())
        self.assertIn("#sidenav-footer > a, #sidenav-footer > form > button { padding-inline", css)

    def test_signed_out_the_login_link_is_still_in_the_list(self):
        page = self.client.get(reverse("beetles_image_browser")).content.decode()
        self.assertNotIn(f'action="{reverse("logout")}"', page)
        rail = page[page.index('<div id="sideItems"'):page.index('id="sidenav-footer"')]
        self.assertIn(f'href="{reverse("login")}"', rail)


class ModelPickerOnTopTests(PageBehaviourCase):
    def page(self):
        self.client.logout()   # the AI page is open to everyone
        return self.client.get(reverse("tool_classify")).content.decode()

    def test_the_model_picker_is_under_the_intro_and_before_the_drop_zone(self):
        page = self.page()
        order = [page.index(marker) for marker in (
            'class="page-intro', 'data-testid="ibbi-link"', 'id="modelSelect"', 'id="canvasContainer"',
            'id="dontKeep"', 'class="example-btn', 'id="classifyForm"')]
        self.assertEqual(order, sorted(order))

    def test_the_picker_is_still_sent_with_the_form(self):
        page = self.page()
        select = re.search(r'<select name="architecture" id="modelSelect"[^>]*>', page).group(0)
        self.assertIn('form="classifyForm"', select)


class FunderLogosWrapTests(PageBehaviourCase):
    def test_the_logo_row_wraps_and_no_logo_is_wider_than_the_screen(self):
        page = self.client.get(reverse("image_browser")).content.decode()
        footer = page[page.index("<footer"):page.index("</footer>")]
        logos = re.findall(r'<img [^>]*grayscale[^>]*>', footer)
        self.assertEqual(len(logos), 4)
        for logo in logos:
            classes = re.search(r'class="([^"]*)"', logo).group(1).split()
            for needed in ("max-w-full", "w-auto", "object-contain", "h-10"):
                self.assertIn(needed, classes, logo)
        row = footer[footer.rfind("<div", 0, footer.index(logos[0])):]
        row = row[:row.index(">")].split('class="')[1].split('"')[0].split()
        for needed in ("flex", "flex-wrap", "justify-center", "max-w-full", "min-w-0"):
            self.assertIn(needed, row)
