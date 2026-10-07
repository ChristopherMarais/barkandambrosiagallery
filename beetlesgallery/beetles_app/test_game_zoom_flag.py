"""
Issue #495: zoom and pan as smooth after a click as with the hand, and the Flag (was "Report") at the bottom left of
every photo, also in the whole-photo view, with nothing else in that corner.
"""
import re

from django.urls import reverse

from beetlesgallery.beetles_app.game_views import FEED_REPORT_REASONS
from beetlesgallery.beetles_app.test_game import GameCase


class ZoomFlagCase(GameCase):
    def page(self, mode="mixed"):
        self.client.force_login(self.user)
        return self.client.get(reverse("game_play", args=[mode])).content.decode()

    def styles(self, page):
        start = page.index("#game { position: fixed")   # the game's own style block
        return page[start:page.index("</style>", start)]

    def zoom_code(self, page):
        return page[page.index("function makeZoomable("):page.index("// Brightness and contrast")]


class SmoothZoomTests(ZoomFlagCase):
    def test_moves_are_drawn_once_a_frame_on_the_gpu(self):
        code = self.zoom_code(self.page())
        self.assertIn("requestAnimationFrame(render)", code)
        self.assertIn("translate3d(", code)
        self.assertIn('canvas.style.willChange = on ? "transform" : ""', code)   # a layer only while it moves
        # the frame is measured once per gesture, not on every move
        self.assertNotIn("clientWidth", code)
        self.assertNotIn("clientHeight", code)

    def test_the_browser_never_takes_over_a_drag(self):
        page = self.page()
        self.assertIn('frame.addEventListener("dragstart", (e) => e.preventDefault())', page)
        self.assertIn('<img id="lightbox-img" alt="Whole image" draggable="false"', page)
        self.assertIn("#photos .frame, #lb-frame { touch-action: none; user-select: none; -webkit-user-select: none;", page)

    def test_a_pinch_carries_on_as_a_drag_and_the_cursor_grabs(self):
        page = self.page()
        code = self.zoom_code(page)
        self.assertIn("if (z > 1) startDrag([...pointers.values()][0]);", code)
        self.assertIn('frame.classList.add("dragging")', code)
        self.assertIn("#photos .frame.dragging, #lb-frame.dragging { cursor: grabbing; }", page)

    def test_only_the_click_steps_are_eased(self):
        code = self.zoom_code(self.page())
        self.assertIn('canvas.style.transition = animate && zoomEase ? "transform 0.18s ease-out" : ""', code)
        self.assertEqual(code.count(", true);"), 1)   # the double-click step, nothing else
        self.assertIn("zoomAt(z > 1 ? 1 : ZOOM_STEP, ...local(e.clientX, e.clientY), true);", code)

    def test_every_photo_still_zooms(self):
        page = self.page()
        self.assertIn('makeZoomable($("lb-frame"), $("lb-zoom")', page)
        self.assertIn("makeZoomable(frame, canvas, () => openLightbox(im.url, im.box, i));", page)

    def test_the_styles_have_no_stray_declarations(self):
        # a declaration outside any rule (left over from a removed one) breaks the rule after it
        css = re.sub(r"/\*.*?\*/", "", self.styles(self.page()), flags=re.S)
        depth, between = 0, ""
        for ch in css:
            if ch == "{":
                self.assertNotIn(";", between, between)   # a selector, never declarations
                depth, between = depth + 1, ""
            elif ch == "}":
                depth -= 1
                self.assertGreaterEqual(depth, 0, "a rule closed that never opened")
                between = ""
            else:
                between += ch
        self.assertEqual(depth, 0)
        self.assertIn("#lb-primary { position: absolute;", css)


class FlagTests(ZoomFlagCase):
    def test_the_flag_sits_bottom_left_and_says_flag(self):
        page = self.page()
        self.assertIn(".report-chip { position: absolute; left: 0.375rem; bottom: 0.375rem;", page)
        # icon-only, the detail page's Flag style (#play-flag); its name is still there for a screen reader
        self.assertIn('report.innerHTML = \'<i class="fi fi-rr-flag"></i><span class="sr-only">Flag</span>\';', page)
        self.assertIn('aria-label="Flag this photo"', page)
        self.assertNotIn("<span>Report</span>", page)
        self.assertNotIn("Report this", page)

    def test_the_more_photos_chip_is_short_on_a_phone(self):
        page = self.page()
        self.assertIn('word.className = "more-word";', page)
        self.assertIn("@media (max-width: 639px) { .more-chip .more-word { display: none; } }", page)

    def test_grid_tiles_have_their_flag_in_the_whole_photo(self):
        page = self.page()
        self.assertIn("if (!grid) holder.querySelectorAll(\".cell\").forEach((cell, i) => {", page)   # no Flag on a tile
        self.assertIn("zoom.addEventListener(\"click\", (e) => { e.stopPropagation(); openTile(i); });", page)

    def test_the_whole_photo_has_its_flag_on_the_photo_with_the_reasons_above(self):
        page = self.page()
        photo = page[page.index('<div id="lb-photo"'):page.index('id="lb-light"')]
        self.assertLess(photo.index('id="lb-frame"'), photo.index('id="report-cog"'))   # beside the zoom, not in it
        self.assertIn('id="report-cog" class="report-chip"', photo)
        self.assertIn('id="report-menu"', photo)
        for value, label in FEED_REPORT_REASONS:
            self.assertIn(f'data-reason="{value}"', photo)
        self.assertIn("#report-cog { left: 0.5rem; bottom: 0.5rem; }", page)
        self.assertIn("#report-menu { left: 0.5rem; bottom: 2.75rem;", page)
        self.assertIn('data-report-url="' + reverse("game_report_item") + '"', page)   # the same endpoint

    def test_nothing_else_takes_a_bottom_left_corner(self):
        page = self.page()
        self.assertIn("#light-btn { position: absolute; right: 0.375rem; bottom: 0.375rem;", page)
        self.assertIn("#light-panel { position: absolute; right: 0.375rem; bottom: 2.75rem;", page)
        self.assertIn(".more-chip { position: absolute; right: 0.375rem; top: 0.375rem;", page)
        self.assertIn(".zoom-chip { position: absolute; right: 0.5rem; top: 0.5rem;", page)   # 8px in, inside the tile (#play-expand)
        # Similarity: A's letter keeps its top right (its other-photos chip moves to the top left); on a phone it
        # drops to A's bottom right, clear of the Flag
        self.assertIn('#game[data-mode="pair"] #photos .cell:nth-child(1) .more-chip { right: auto; left: 0.375rem; }', page)
        self.assertIn('#game[data-mode="pair"] #photos .cell:nth-child(1) .ab-tag { top: auto; bottom: 0.375rem; }', page)
        self.assertNotIn(".report-chip { right: auto", page)

    def test_the_tips_point_at_the_new_corner(self):
        page = self.page()
        self.assertIn('<i class="fi fi-rr-flag"></i> Flag</strong> at the <strong>bottom left of the photo</strong>', page)
        self.assertNotIn("top right of the photo", page)
