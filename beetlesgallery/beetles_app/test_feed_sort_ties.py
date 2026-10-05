"""
Images added in the same instant (one import, a coarse clock) sort the same way every time in the annotation feed,
and "Oldest added" is exactly "Newest added" turned round.
"""
from django.utils import timezone

from beetlesgallery.beetles_app.models import ImageAsset
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle

URL = "/api/v1/beetles/images-with-annotations/"


class FeedSortTieTests(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)
        images = [make_beetle().image_asset for _ in range(4)]
        ImageAsset.objects.filter(pk__in=[i.pk for i in images]).update(created_at=timezone.now())

    def order(self, sort):
        response = self.client.get(f"{URL}?ordering={sort}")
        self.assertEqual(response.status_code, 200)
        return [r["image_asset_id"] for r in response.json()["results"]]

    def test_oldest_is_newest_turned_round_when_images_share_a_timestamp(self):
        newest = self.order("newest")
        self.assertEqual(len(newest), 4)
        self.assertEqual(self.order("newest"), newest)          # the same order every time
        self.assertEqual(self.order("oldest"), newest[::-1])
