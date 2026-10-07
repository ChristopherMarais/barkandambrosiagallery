"""
Round 7, after the owner played the game: the recap's way out says it goes to the game's home, the Naming photo sits
in the middle of its space, Tab is Back and Backspace is Skip (never while typing or on a list), the Esc pill is even
on both sides, a grid tile shows its Flag and its other photos under the pointer, the photo to name has its own icon
(not Focus's bullseye), and every icon sits dead centre in its pill.
"""
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

PAGE = (Path(settings.BASE_DIR) / "beetlesgallery" / "templates" / "beetles" / "game_play.html").read_text(encoding="utf-8")
TOUR = (Path(settings.BASE_DIR) / "beetlesgallery" / "static" / "js" / "game_tour.js").read_text(encoding="utf-8")
GRID = '#game:is([data-mode="odd"], [data-mode="select"]) #photos'


def between(start, end):
    return PAGE[PAGE.index(start):PAGE.index(end, PAGE.index(start))]


class RecapHomeLinkTests(SimpleTestCase):   # C1
    def test_the_link_names_the_games_home_not_just_the_game(self):
        link = between('data-testid="recap-home"', "</a>")
        self.assertIn("{{ game_name }} home", link)
        self.assertIn("{% url 'game_home' %}\" class=\"px-6 py-3.5", PAGE)   # where it goes: the game's home, /game/


class NamingPhotoTests(SimpleTestCase):   # C2
    def test_the_photo_is_centred_in_its_space(self):
        self.assertIn('#game[data-mode="classify"] #photo-area { justify-content: center; }', PAGE)
        self.assertIn('#game[data-mode="classify"] #photos { flex: 0 1 auto; max-height: 45vh;', PAGE)


class KeyTests(SimpleTestCase):   # C3
    def test_b_is_back_and_backspace_is_skip(self):
        # Tab is never the game's: it moves between the buttons (owner), so Back is B (or Z)
        self.assertIn('skip: ["Backspace", "Delete", "End", "x"], back: ["b", "z"],', PAGE)
        keys = between("const KEYS = {", "};")
        self.assertNotIn('"Tab"', keys)

    def test_the_buttons_show_the_new_keys(self):
        actions = between('<div id="actions">', 'id="submit"')
        self.assertIn('<span id="skip-text">Skip</span><kbd class="kbd" title="Backspace">&#9003;</kbd>', actions)
        self.assertIn('Back<kbd class="kbd">Ctrl</kbd>', actions)
        self.assertNotIn(">Del</kbd>", PAGE)

    def test_a_lone_ctrl_tap_is_back_and_shortcuts_are_untouched(self):
        # owner: Ctrl for Back. It counts only when let go with no other key pressed meanwhile (Ctrl+C still copies)
        self.assertIn('ctrlAlone = e.key === "Control" && !e.repeat ? true : (e.key === "Control" && ctrlAlone);', PAGE)
        self.assertIn('if (e.key !== "Control" || !ctrlAlone) return;', PAGE)
        keyup = between('document.addEventListener("keyup", (e) => {', "});")
        self.assertIn("showPrevious()", keyup)
        self.assertIn("hidePrevious()", keyup)
        self.assertIn("isTyping(e.target)", keyup)

    def test_tab_and_form_fields_keep_their_own_jobs(self):
        handler = between('document.addEventListener("keydown", (e) => {\n    if (e.key === "Escape")', "// the game page opened")
        self.assertIn("if (isTyping(t)) return;", handler)   # inputs and text areas: typing, Tab between fields
        self.assertIn('if (e.key === "Backspace" && (e.shiftKey || (t && t.tagName === "SELECT"))) return;', handler)
        self.assertNotIn('"Tab"', handler)   # Tab moves focus between the buttons, as on any page
        self.assertLess(handler.index("if (isTyping(t)) return;"), handler.index("const pressed"))

    def test_a_held_key_is_one_press(self):
        self.assertIn('(pressed === "next" || pressed === "skip" || pressed === "back") && e.repeat', PAGE)
        self.assertIn("if (!e.repeat) hidePrevious();", PAGE)


class ExitPillTests(SimpleTestCase):   # C4
    def test_the_cross_and_the_key_cap_sit_evenly(self):
        self.assertIn('id="exit" class="inline-flex items-center justify-center"', PAGE)
        self.assertIn("#exit > i { display: flex; align-items: center; justify-content: center; width: 1.125rem; height: 1.125rem;", PAGE)
        self.assertIn("#game.has-kbd #exit { gap: 0.375rem; padding: 0 0.625rem 0 0.375rem; }", PAGE)
        self.assertIn("#game.has-kbd #exit .kbd { margin-left: 0; }", PAGE)


class GridTileChipTests(SimpleTestCase):   # C5
    def test_every_tile_gets_its_flag_and_other_photos(self):
        self.assertIn("      if (im.more) {", PAGE)
        self.assertNotIn("if (im.more && !grid)", PAGE)
        self.assertNotIn('if (!grid) holder.querySelectorAll(".cell")', PAGE)

    def test_they_show_under_the_pointer_or_after_a_tap(self):
        self.assertIn(GRID + " .cell > :is(.report-chip, .more-chip) { opacity: 0; pointer-events: none;", PAGE)
        self.assertIn("@media (hover: hover) { " + GRID + " .cell:hover > :is(.report-chip, .more-chip) { opacity: 1; pointer-events: auto; } }", PAGE)
        self.assertIn(GRID + " .cell:is(.chips-on, .key-cursor) > :is(.report-chip, .more-chip),", PAGE)

    def test_the_other_photos_chip_takes_the_free_corner(self):
        self.assertIn(GRID + " .more-chip { top: auto; right: 0.375rem; bottom: 0.375rem; }", PAGE)
        self.assertIn("#photos.revealing .more-chip, #photos .cell.flagged .more-chip { visibility: hidden; }", PAGE)

    def test_the_tour_skips_a_see_through_flag(self):
        self.assertIn('style.opacity !== "0"', TOUR)


class PhotoToNameIconTests(SimpleTestCase):   # C6
    def test_the_target_has_its_own_icon(self):
        marker = between('<span id="lb-primary"', "</span>")
        self.assertIn("fi-rr-location-crosshairs", marker)
        self.assertNotIn("fi-rr-bullseye", marker)
        self.assertIn('<i class="fi fi-rr-bullseye" id="focus-icon"></i>', PAGE)   # Focus keeps the bullseye


class IconCentringTests(SimpleTestCase):   # C7
    def test_every_icon_is_a_box_of_its_own(self):
        self.assertIn(":is(#game, #lightbox, #recap, #toasts, .level-pop) .fi:not(.hidden) { display: inline-flex; align-items: center; justify-content: center; line-height: 1; }", PAGE)
        self.assertIn(":is(#game, #lightbox, #recap, #toasts, .level-pop) .fi::before { display: block; line-height: 1; }", PAGE)

    def test_the_pills_centre_both_ways(self):
        self.assertIn("#light-btn, .more-chip, #chip, #combo, #level-chip, #focus-btn, #lb-light, .flag-badge, .rv-seen { justify-content: center; }", PAGE)
        self.assertIn('#lightbox > button[aria-label="Close"], .lb-nav { display: inline-flex; align-items: center; justify-content: center;', PAGE)
        for pill in (".report-chip {", ".zoom-chip {", ".icon-btn {", "#lb-primary {", ".tap-mark {"):
            rule = PAGE[PAGE.index("  " + pill):]
            rule = rule[:rule.index("}")]
            self.assertIn("align-items: center; justify-content: center;", rule, pill)

    def test_a_hidden_icon_stays_hidden(self):
        self.assertIn('<i class="fi fi-rr-check hidden" id="chip-check"', PAGE)
        self.assertIn(".fi:not(.hidden)", PAGE)
