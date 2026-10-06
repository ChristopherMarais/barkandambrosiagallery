"""Confetti bursts (#491): bursts run side by side in one loop, bigger for a species and a level-up, sized by points."""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

BASE = Path(settings.BASE_DIR) / "beetlesgallery"
JS = (BASE / "static" / "js" / "beetle_confetti.js").read_text()
PLAY = (BASE / "templates" / "beetles" / "game_play.html").read_text()


def body(name):
    """The source of the JS function ``name`` (up to the closing brace at its own indent)."""
    match = re.search(r"\n( *)function " + name + r"\b.*?\n\1\}", JS, re.S)
    return match.group(0) if match else ""


class ConcurrentBurstTests(SimpleTestCase):
    def test_the_public_call_is_unchanged(self):
        self.assertIn("window.beetleConfetti = function (canvas, kind, size)", JS)

    def test_a_new_burst_never_resets_the_canvas(self):
        # setting canvas.width clears what other bursts drew: only fit() does it, and only when the window changed
        self.assertEqual(JS.count("canvas.width ="), 1)
        fit = body("fit")
        self.assertIn("canvas.width = w", fit)
        self.assertIn("canvas.width !== w || canvas.height !== h", fit)
        call = JS.split("window.beetleConfetti = function", 1)[1]
        self.assertNotIn("canvas.width =", call)
        self.assertIn("fit(scene)", body("run"))   # resize-aware: every frame matches the window

    def test_one_loop_for_all_bursts(self):
        self.assertEqual(JS.count("requestAnimationFrame("), 2)   # the start and the next frame, both in run()
        run = body("run")
        self.assertIn("if (scene.running) return;", run)
        self.assertEqual(run.count("requestAnimationFrame("), 2)
        call = JS.split("window.beetleConfetti = function", 1)[1]
        self.assertIn("scene.pieces.push(", call)
        self.assertIn("run(scene)", call)

    def test_total_pieces_are_capped(self):
        self.assertIn("maxPieces()", JS)
        self.assertIn("pointer: coarse", body("maxPieces"))


class IntensityTests(SimpleTestCase):
    def test_every_kind_has_a_palette(self):
        for kind in ("partial", "validated", "level", "plain"):
            self.assertRegex(JS, kind + r": \[\"#")
        self.assertIn('kind === "beetles" ? "validated"', JS)   # the history page's old name still works

    def test_size_scales_count_piece_size_and_spread(self):
        waves = body("waves")
        for kind in ("partial", "validated", "plain"):
            self.assertRegex(waves, r'\["' + kind + r'", \d+ \+ Math\.round\(\d+ \* size\)')
        piece = body("piece")
        self.assertIn("* (0.65 + 0.35 * size)", piece)   # piece size
        self.assertIn("7 * size", piece)                 # how far they fly
        self.assertIn("Math.max(0.2, Math.min(1, Number(size) || 1))", JS)

    def test_a_species_gets_a_second_wave_at_full_size(self):
        self.assertIn('if (size >= 0.95) list.push(["validated"', body("waves"))

    def test_the_level_up_is_the_biggest_from_both_sides_and_the_centre(self):
        waves = body("waves")
        level = waves.split('if (kind === "level")', 1)[1].split("];", 1)[0]
        for side in ('"left"', '"right"', '"centre"'):
            self.assertIn(side, level)
        self.assertIn("3500", level)
        self.assertGreater(sum(int(n) for n in re.findall(r'\["(?:level|plain)", (\d+)', level)), 200)

    def test_the_logo_mask_is_kept(self):
        self.assertIn("BEETLE_LOGO_URL", JS)
        self.assertIn("function buildMask", JS)
        self.assertIn("drawBeetle(ctx", JS)


class ReducedMotionAndWrapperTests(SimpleTestCase):
    def test_reduced_motion_means_no_confetti(self):
        call = JS.split("window.beetleConfetti = function", 1)[1]
        self.assertIn('matchMedia("(prefers-reduced-motion: reduce)").matches) return;', call.split("\n", 2)[1])

    def test_the_page_wrapper_keeps_its_signature(self):
        self.assertIn("function confetti(kind, size) {", PLAY)
        self.assertIn('window.beetleConfetti($("confetti"), kind === "strong" ? "plain" : (kind || "validated"), size);', PLAY)
        self.assertIn("if (reduceMotion || !window.beetleConfetti) return;", PLAY)
