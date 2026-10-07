"""
Basic accounts and access granted area by area: a Basic account (approved automatically on email confirmation) has
the image browser without specimen pages, the taxonomy browser, the interactions page, the classifier and the game;
everything else is an area a superuser grants, to staff and non-staff alike.
"""
import importlib

from django.apps import apps
from django.contrib.auth import get_user_model
from django.urls import reverse

from beetlesgallery.beetles_app import areas
from beetlesgallery.beetles_app.models import AreaGrant
from beetlesgallery.beetles_app.test_pages import PageTestCase
from beetlesgallery.beetles_app.testing import make_beetle, make_image

User = get_user_model()


class BasicAccountTests(PageTestCase):
    def setUp(self):
        super().setUp()
        self.basic = User.objects.create_user("basic", password="pw")
        self.client.force_login(self.basic)

    def test_basic_pages_open(self):
        for name in ("beetles_image_browser", "taxonomy_browser", "interactions_preview", "game_home", "tool_classify"):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_specimen_pages_and_downloads_need_their_area(self):
        beetle = make_beetle(image=make_image())
        res = self.client.get(reverse("beetle_detail", args=[beetle.id]))
        self.assertContains(res, 'data-testid="needs-access"', status_code=403)
        self.assertContains(res, reverse("request_access"), status_code=403)
        self.assertEqual(self.client.post(reverse("start_batch_download"), {"selection_mode": "ids"}).status_code, 403)
        AreaGrant.objects.create(user=self.basic, area=areas.DETAILS)
        self.assertEqual(self.client.get(reverse("beetle_detail", args=[beetle.id])).status_code, 200)

    def test_the_gallery_offers_no_specimen_links_or_downloads_to_basic_accounts(self):
        beetle = make_beetle(image=make_image())
        page = self.client.get(reverse("beetles_image_browser")).content.decode()
        self.assertNotIn(reverse("beetle_detail", args=[beetle.id]), page)
        self.assertNotIn("Download All", page)


class GranularStaffTests(PageTestCase):
    def test_a_curator_has_only_what_they_were_given(self):
        curator = User.objects.create_user("boxer", password="pw", is_staff=True)
        AreaGrant.objects.create(user=curator, area=areas.BOXES)
        self.assertTrue(areas.has_area(curator, areas.BOXES))
        self.assertFalse(areas.has_area(curator, areas.ANNOTATE))
        self.assertFalse(areas.has_area(curator, areas.UPLOAD))
        self.client.force_login(curator)
        self.assertEqual(self.client.get(reverse("upload")).status_code, 403)

    def test_editing_records_includes_editing_boxes(self):
        AreaGrant.objects.create(user=self.user, area=areas.ANNOTATE)
        self.assertTrue(areas.has_area(self.user, areas.BOXES))

    def test_the_species_tables_are_their_own_permission(self):
        self.client.force_login(self.staff)   # a curator with every other area
        self.assertEqual(self.client.get(reverse("admin_valid_species")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("data_management")), "Species tables")
        AreaGrant.objects.create(user=self.staff, area=areas.SPECIES)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("admin_valid_species")).status_code, 200)
        self.assertContains(self.client.get(reverse("data_management")), "Species tables")

    def test_superusers_have_everything(self):
        for key in areas.KEYS:
            self.assertTrue(areas.has_area(self.superuser, key), key)


class ExistingAccountsKeepTheirAccessTests(PageTestCase):
    def test_the_migration_gives_members_and_curators_what_their_role_gave_them(self):
        member = User.objects.create_user("old-member", password="pw")
        curator = User.objects.create_user("old-curator", password="pw", is_staff=True)
        waiting = User.objects.create_user("waiting", password="pw", is_active=False)
        boss = User.objects.create_superuser("old-boss", password="pw")
        migration = importlib.import_module("beetlesgallery.beetles_app.migrations.0037_area_grants_for_existing_accounts")
        migration.grant(apps, None)
        migration.grant(apps, None)   # running it again adds nothing twice
        have = lambda u: sorted(AreaGrant.objects.filter(user=u).values_list("area", flat=True))   # noqa: E731
        self.assertEqual(have(member), sorted(areas.MEMBER_AREAS))
        self.assertEqual(have(curator), sorted(migration.CURATOR_AREAS))
        self.assertNotIn(areas.SPECIES, have(curator))
        self.assertEqual((have(waiting), have(boss)), ([], []))
