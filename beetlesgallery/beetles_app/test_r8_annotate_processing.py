"""The annotation page's Generate (IBBI-AI) shows that it is working (owner: it was unclear): the button turns into a
spinner with "Processing…" and a blue IBBI-AI pill shows under it until the answer comes back."""
from pathlib import Path

from django.test import SimpleTestCase

PAGE = (Path(__file__).resolve().parent.parent / "templates" / "beetles" / "tool_annotate.html").read_text(encoding="utf-8")


class GenerateProcessingTests(SimpleTestCase):
    def test_a_processing_pill_under_the_button(self):
        self.assertIn('<span id="classify-processing" class="hidden inline-flex items-center justify-center', PAGE)
        self.assertIn("IBBI-AI is processing this image…", PAGE)

    def test_the_button_shows_processing_and_comes_back(self):
        fn = PAGE[PAGE.index("async function classifyCurrentImage()"):PAGE.index("async function acquireLock")]
        self.assertIn("btn.innerHTML = '<i class=\"fi fi-rr-spinner animate-spin\"></i><span>Processing…</span>';", fn)
        self.assertLess(fn.index("pill.classList.remove('hidden')"), fn.index("await fetch"))
        finally_block = fn[fn.index("} finally {"):]
        for back in ("btn.innerHTML = idle;", "pill.classList.add('hidden');", "btn.disabled = false;"):
            self.assertIn(back, finally_block)
