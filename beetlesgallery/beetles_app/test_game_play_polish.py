"""
The play page, polished after the owner played it on a phone: the loading lines stay up long enough to read (longer
for a long fact), the Exit cross sits in the middle of its pill, a Naming list that still says Not sure opens its
search at once, and under the "AI ID" / "Player ID" headings the rows don't say it again.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

PAGE = (Path(settings.BASE_DIR) / "beetlesgallery" / "templates" / "beetles" / "game_play.html").read_text(encoding="utf-8")


def between(start, end):
    return PAGE[PAGE.index(start):PAGE.index(end, PAGE.index(start))]


def constant(name):
    return int(re.search(name + r" = (\d+)", PAGE).group(1))


class LoadingLineTimeTests(SimpleTestCase):
    def ms(self, text):   # the page's loadingMs(), in Python
        base, per, low, high = (constant(n) for n in ("LOADING_BASE_MS", "LOADING_CHAR_MS", "LOADING_MIN_MS", "LOADING_MAX_MS"))
        return min(high, max(low, base + per * len(text)))

    def test_each_line_waits_by_its_length_not_a_fixed_beat(self):
        self.assertNotIn("LOADING_LINE_MS", PAGE)
        block = between("function loadingLines()", "// ---------- the feed")
        self.assertNotIn("setInterval", block)
        self.assertIn("loadingTimer = setTimeout(step, loadingMs(line.textContent));", block)
        self.assertIn("return Math.min(LOADING_MAX_MS, Math.max(LOADING_MIN_MS, LOADING_BASE_MS + LOADING_CHAR_MS * text.length));",
                      PAGE)

    def test_short_lines_go_sooner_and_long_facts_stay_readable(self):
        lines = re.findall(r'"([^"]+)"', between("const LOADING_LINES", "const LOADING_FACTS"))
        facts = re.findall(r'"([^"]+)"', between("const LOADING_FACTS", "const LOADING_BASE_MS"))
        self.assertTrue(all(3000 <= self.ms(t) <= 9000 for t in lines + facts))
        self.assertLess(max(self.ms(t) for t in lines), min(self.ms(t) for t in facts))
        self.assertGreaterEqual(min(self.ms(t) for t in facts), 5000)   # even the shortest fact gets 5 seconds

    def test_reduced_motion_still_keeps_the_first_line(self):
        block = between("function loadingLines()", "// ---------- the feed")
        self.assertIn("if (still) return;   // reduced motion: the first line stays put", block)


class ExitCentredTests(SimpleTestCase):
    def test_the_exit_cross_is_centred_in_its_pill(self):
        self.assertIn('id="exit" class="inline-flex items-center justify-center"', PAGE)
        self.assertIn("#exit { flex-shrink: 0; height: 2.5rem; min-width: 2.5rem; padding: 0 0.5rem;", PAGE)
        self.assertIn("#exit > i { display: flex; align-items: center; justify-content: center;", PAGE)
        self.assertNotIn("padding: 0 1rem 0 0.75rem", PAGE)   # the uneven padding that pushed it off the middle


class NotSureSearchesTests(SimpleTestCase):
    def test_a_tap_on_a_list_that_says_not_sure_opens_its_search(self):
        self.assertIn('return !busy && !select.value && !select.disabled && select.closest(".rank-row").classList.contains("findable");', PAGE)
        block = between("function searchFirst(select)", "// ---------- rank steps")
        self.assertIn('select.addEventListener("touchend", (e) => {', block)
        self.assertIn('select.addEventListener("mousedown", (e) => {', block)
        self.assertEqual(block.count("openFinder(rank);"), 2)
        self.assertEqual(block.count("e.preventDefault();"), 2)   # the phone's own picker does not open first

    def test_a_scroll_is_not_a_tap(self):
        self.assertIn("Math.abs(t.clientX - touchAt[0]) < 10 && Math.abs(t.clientY - touchAt[1]) < 10", PAGE)

    def test_the_rest_of_not_sure_is_unchanged(self):
        self.assertIn('none.textContent = opts.length ? "Not sure" : (rank === "species" ? "Pick a genus first" : "—");', PAGE)
        self.assertIn('btn.addEventListener("click", () => (finding === btn.dataset.openFind ? closeFinder() : openFinder(btn.dataset.openFind)));', PAGE)


class NoRepeatedIdLabelTests(SimpleTestCase):
    def test_the_headings_stay_and_the_rows_do_not_repeat_them(self):
        self.assertIn('const ID_COLUMN = { ai: "AI ID", players: "Player ID" };', PAGE)
        self.assertIn('node("span", "hdr", node("span", "spacer"), node("span", "cols", ...cols.map((k) => node("span", "c " + k, sourceDot(k), ID_COLUMN[k]))))', PAGE)
        self.assertIn('const idType = name && r.label && !cols.length ? node("span", "idl", r.label) : null;', PAGE)
