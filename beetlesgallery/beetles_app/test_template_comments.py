"""A Django {# #} comment must fit on one line; one spread over two lines is printed on the page as text.
Longer notes go in {% comment %} ... {% endcomment %}."""
from pathlib import Path

from django.test import SimpleTestCase

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


class TemplateCommentTests(SimpleTestCase):
    def test_no_template_comment_spans_two_lines(self):
        offenders = []
        for path in sorted(TEMPLATES.rglob("*.html")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                start = line.find("{#")
                if start != -1 and "#}" not in line[start:]:
                    offenders.append(f"{path.relative_to(TEMPLATES)}:{number}")
        self.assertEqual(offenders, [], "use {% comment %} for notes longer than one line")
