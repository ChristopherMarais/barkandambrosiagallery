"""Round 7 of the game's review (owner): the whole photo opens at its full size and comes into focus in place (B1),
thicker rings round the grid photos (B2), every name in full with the AI's and the players' names in their colours
and the columns never squeezing the name (B3), Back's photos built like the review's (B4), and names that always
start on view, a hover or a tap only clearing them (B5)."""
import re
from pathlib import Path

from django.test import SimpleTestCase

GAME_PLAY = Path(__file__).resolve().parent.parent / "templates" / "beetles" / "game_play.html"


def js_function(page, name):
    """The body of ``function name(...) {...}`` in the page, up to the next top-level function."""
    start = page.index(f"function {name}(")
    end = page.find("\n  function ", start + 1)
    return page[start:end if end != -1 else len(page)]


class R7ReviewTests(SimpleTestCase):
    def setUp(self):
        self.page = GAME_PLAY.read_text(encoding="utf-8")

    # B1: the whole photo
    def test_the_photo_takes_its_full_size_from_the_first_picture(self):
        fit = js_function(self.page, "fitLightbox")
        self.assertIn("lbAspect = w / h", fit)
        self.assertIn('img.style.width = Math.round(width) + "px";', fit)
        self.assertIn('img.style.height = Math.round(width / lbAspect) + "px";', fit)
        show = js_function(self.page, "showPhoto")
        self.assertIn("fitLightbox(pic.naturalWidth, pic.naturalHeight);", show)
        self.assertIn("if (p.thumb) loadImage(p.thumb).then(soft, () => {});", show)

    def test_the_sharp_photo_comes_into_focus_in_place(self):
        self.assertIn('<span id="lb-soft" aria-hidden="true"><img id="lb-soft-img" alt="" draggable="false"></span>', self.page)
        self.assertIn("#lb-soft { position: absolute; inset: 0;", self.page)
        self.assertIn("filter: blur(6px); opacity: 0; transition: opacity 0.35s ease-out; }", self.page)
        self.assertIn("#lb-photo.soft #lb-soft { opacity: 1; transition: none; }", self.page)
        show = js_function(self.page, "showPhoto")
        self.assertIn('photo.classList.add("soft");', show)
        self.assertIn('photo.classList.remove("soft");', show)

    def test_the_names_are_set_before_the_photo_is_sized(self):
        opened = js_function(self.page, "openLightbox")
        self.assertLess(opened.index('$("lightbox").classList.toggle("with-names", !!names);'), opened.index("showPhoto("))

    # B2: the rings
    def test_the_rings_are_thicker(self):
        self.assertIn("border-radius: inherit; border: 4px solid var(--ring, transparent);", self.page)
        self.assertIn(".cell:is(.ring-missed, .ring-avoided, .ring-unknown)::after { border-style: dashed; border-width: 3px;", self.page)
        self.assertNotIn("border: 3px solid var(--ring", self.page)

    # B3: the names
    def test_every_name_in_full(self):
        label = js_function(self.page, "namesLabel")
        self.assertNotIn('genus[0] + ". "', label)
        self.assertNotIn("text-overflow: ellipsis; white-space: nowrap; font-weight: 600;", self.page)
        self.assertIn(":is(.rv-names, .lb-names) .nm { flex: 1 1 0; min-width: min(8rem, 100% - 3.5rem); white-space: normal;", self.page)
        self.assertIn(":is(.rv-names, .lb-names) > span { flex-wrap: wrap;", self.page)

    def test_the_columns_are_one_group_that_moves_under_the_name(self):
        label = js_function(self.page, "namesLabel")
        self.assertIn('cells.length ? node("span", "cols", ...cells) : null', label)
        self.assertIn('node("span", "cols", ...cols.map(', label)
        self.assertIn(":is(.rv-names, .lb-names) .cols { flex: none; display: flex;", self.page)
        self.assertIn("margin-left: auto; max-width: 100%; }", self.page)

    def test_the_ai_and_player_names_in_their_colours(self):
        self.assertIn(":is(.rv-names, .lb-names) .c.ai .alt { color: #bfdbfe; text-decoration-color: #3b82f6; }", self.page)
        self.assertIn(":is(.rv-names, .lb-names) .c.players .alt { color: #e9d5ff; text-decoration-color: #a855f7; }", self.page)
        self.assertIn('cell.prepend(node("span", "alt", view.name));', js_function(self.page, "columnCell"))

    def test_the_rank_tags_and_headings_stay(self):
        self.assertIn('const RANK_ABBR = { subfamily: "SF", tribe: "T", genus: "G", species: "S" };', self.page)
        self.assertIn('const idType = name && r.label && !cols.length ? node("span", "idl", r.label) : null;', self.page)

    # B4: Back
    def test_back_builds_its_photos_like_the_review(self):
        self.assertIn("const cells = previous.images.map((im, i) => reviewCell(im, i, n, !!previous.sharp_on_zoom));",
                      self.page)
        cell = js_function(self.page, "reviewCell")
        self.assertIn('const frame = node("button", "frame");', cell)
        self.assertIn("makeZoomable(frame, canvas, () => openReviewPhoto(c), false, sharpOnZoom ? sharpen : null);", cell)
        self.assertIn("loadCrop(im)", cell)
        self.assertIn("#previous-photos button.frame { position: relative; width: 100%; height: 100%;", self.page)
        self.assertIn("#previous-photos .frame { touch-action: none;", self.page)
        self.assertIn('reviewPhotos($("previous-photos"));', self.page)

    # B5: names start on view
    def test_names_always_start_on_view(self):
        tile = js_function(self.page, "nameTile")
        self.assertIn('c.classList.remove("names-off");', tile)
        self.assertLess(tile.index('c.classList.remove("names-off");'), tile.index("c.appendChild(namesLabel(b, review));"))

    def test_no_rule_shows_names_on_hover_or_on_names_off(self):
        self.assertIsNone(re.search(r":hover \.rv-names \{ opacity: 1", self.page))
        self.assertIsNone(re.search(r"names-off \.rv-names \{ opacity: 1", self.page))
        self.assertIn("@media (hover: hover) { .cell:hover .rv-names { opacity: 0; } }", self.page)
        self.assertIn(".cell.names-off .rv-names { opacity: 0; }", self.page)
        grid25 = re.search(r'\[data-size="25"\] \.cell > \.rv-names \{[^}]*\}', self.page).group(0)
        self.assertNotIn("opacity: 0", grid25)

    # E6: the round button's icon dead centre
    def test_the_whole_photo_button_icon_is_centred(self):
        self.assertIn(".cell > .rv-whole > i { display: flex; line-height: 1; }", self.page)
