"""
Small image first, then the full one (#607), on the specimen page, the annotation page and the AI page: the thumbnail
shows at once at the photo's own size, so nothing jumps, and the full photo replaces it once it has loaded.
"""
import json
from pathlib import Path

from django.conf import settings
from django.urls import reverse

from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_image

BASE = Path(settings.BASE_DIR) / "beetlesgallery"
SCRIPT = (BASE / "static" / "js" / "progressive_image.js").read_text(encoding="utf-8")
TEMPLATES = BASE / "templates" / "beetles"


class SpecimenPageTests(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def photo(self, page):
        start = page.index('data-testid="roi-photo-img"')
        return page[page.rindex("<img", 0, start):page.index("/>", start)]

    def test_the_thumbnail_shows_first_at_the_photos_size(self):
        asset = make_image(image_file="originals/aa/bb/photo.jpg", thumb_small="thumbnails/aa/bb/photo_96.webp",
                           image_width=4000, image_height=3000)
        beetle = make_beetle(image=asset)
        img = self.photo(self.client.get(reverse("beetle_detail", args=[beetle.id])).content.decode())
        self.assertIn('src="/media/thumbnails/aa/bb/photo_96.webp"', img)
        self.assertIn('data-full-src="/media/originals/aa/bb/photo.jpg"', img)
        self.assertIn("aspect-ratio: 4000 / 3000", img)
        self.assertIn("width: min(4000px, calc(70vh * 4000 / 3000))", img)

    def test_without_a_thumbnail_or_a_size_the_full_photo_shows_as_before(self):
        for fields in ({"thumb_small": "thumbnails/aa/bb/x_96.webp"}, {"image_width": 4000, "image_height": 3000}):
            asset = make_image(image_file="originals/aa/bb/photo.jpg", **fields)
            img = self.photo(self.client.get(reverse("beetle_detail", args=[make_beetle(image=asset).id])).content.decode())
            self.assertIn('src="/media/originals/aa/bb/photo.jpg"', img)
            self.assertNotIn("data-full-src", img)

    def test_every_page_has_the_swap(self):
        page = self.client.get(reverse("image_browser")).content.decode()
        self.assertIn("js/progressive_image.js", page)
        self.assertIn('img[data-full-src]', SCRIPT)
        self.assertIn("full.decode()", SCRIPT)   # swapped once decoded, so it never blanks


class AiPageTests(PageBehaviourCase):
    def test_the_gallery_photo_comes_with_its_thumbnail_and_size(self):
        asset = make_image(image_file="originals/aa/bb/photo.jpg", thumb_small="thumbnails/aa/bb/photo_96.webp",
                           image_width=1200, image_height=800)
        page = self.client.get(reverse("tool_classify"), {"asset": str(asset.id)}).content.decode()
        tag = '<script id="gallery-photo" type="application/json">'
        start = page.index(tag) + len(tag)
        photo = json.loads(page[start:page.index("</script>", start)])
        self.assertEqual((photo["thumb"], photo["width"], photo["height"]),
                         ("/media/thumbnails/aa/bb/photo_96.webp", 1200, 800))
        self.assertIn("img.src = photo.thumb;", page)


class AnnotationPageTests(PageBehaviourCase):
    def test_the_canvas_draws_the_thumbnail_until_the_photo_is_in(self):
        page = (TEMPLATES / "tool_annotate.html").read_text(encoding="utf-8")
        self.assertIn("const thumbUrl = img.thumbnail_url || state.currentImageDetails.thumb_small;", page)
        self.assertIn("thumb.onload = () => { if (!full) reveal(thumb, fullW, fullH); };", page)
        self.assertIn("if (state.selectedImageId !== wanted) return;", page)
