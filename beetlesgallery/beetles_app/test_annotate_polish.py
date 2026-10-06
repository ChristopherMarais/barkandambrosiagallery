"""Issue #537: on the annotation page the label history wraps on narrow screens, and the Alias ID label is short."""
import re

from django.urls import reverse

from beetlesgallery.beetles_app.test_game import GameCase


class AnnotatePolishTests(GameCase):
    def page(self):
        self.client.force_login(self.staff)
        return self.client.get(reverse("tool_annotate")).content.decode()

    def test_label_history_lines_wrap_instead_of_truncating(self):
        page = self.page()
        start = page.index("function labelHistoryLine(e)")
        body = page[start:page.index("\n}\n", start)]
        line = re.search(r"<li class=\"([^\"]*)\" data-kind=", body)
        self.assertIsNotNone(line)
        classes = line.group(1).split()
        self.assertIn("break-words", classes)            # long names split rather than run off the panel
        for clipping in ("truncate", "whitespace-nowrap", "text-ellipsis", "overflow-hidden"):
            self.assertNotIn(clipping, classes)

    def test_alias_id_label_has_no_aside(self):
        page = self.page()
        self.assertNotIn("your own ID for it", page)
        self.assertIn(">Alias ID</label>", page)
        # the explanation stays available as the label's tooltip
        self.assertIn('title="Your own ID for this record', page)
