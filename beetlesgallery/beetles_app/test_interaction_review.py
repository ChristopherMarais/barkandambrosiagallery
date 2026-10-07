"""Reviewing proposed interactions: the page, the decisions, and showing accepted ones on the interactions page."""
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from beetlesgallery.beetles_app import interaction_review as review
from beetlesgallery.beetles_app.models import InteractionProposal, PathogenInteraction
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_taxon


class ReviewCase(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        self.typo = make_taxon(valid_species_id="1733", genus="Ips", species="typographus", scientific_name="Ips typographus")
        self.affinis = make_taxon(valid_species_id="2210", genus="Xyleborus", species="affinis", scientific_name="Xyleborus affinis")

    def proposal(self, partner="Ophiostoma polonicum", doi="10.1000/a", beetle=None, **fields):
        beetle = beetle or self.typo
        values = dict(
            beetle_name=beetle.scientific_name, beetle_valid_species_id=beetle.valid_species_id, taxon=beetle,
            partner_name=partner, category="Fungi", relationship="associate", source_key=doi, source_doi=doi,
            source_url=f"https://europepmc.org/article/MED/{abs(hash(doi)) % 10**6}", source_title=f"Paper {doi}",
            source_authors="Smith J, Jones K, Lee A", source_journal="J Beetles", source_year=2020, source_db="europepmc",
            open_access=True, evidence=f"Ips typographus carries {partner} in its mycangia.", evidence_location="abstract",
            score=0.7, collector="test",
        )
        values.update(fields)
        return InteractionProposal.objects.create(**values)

    def decide(self, claim, decision, user=None, **fields):
        self.client.force_login(user or self.staff)
        return self.client.post(reverse("interaction_review"), {"claim": claim, "decision": decision, **fields}, follow=True)

    def key(self, partner="Ophiostoma polonicum", beetle=None):
        return review.claim_key((beetle or self.typo).valid_species_id, partner)


class PermissionTests(ReviewCase):
    def test_only_staff_can_review_or_decide(self):
        p = self.proposal()
        url = reverse("interaction_review")
        self.assertRedirectsToLogin(self.client.get(url))
        self.client.force_login(self.user)
        self.assertRedirectsToLogin(self.client.get(url))
        self.assertRedirectsToLogin(self.client.post(url, {"claim": self.key(), "decision": "accept"}))
        p.refresh_from_db()
        self.assertEqual(p.status, "proposed")
        self.assertFalse(PathogenInteraction.objects.exists())
        for account in (self.staff, self.superuser):
            self.client.force_login(account)
            self.assertEqual(self.client.get(url).status_code, 200)


class PageTests(ReviewCase):
    def get(self, **params):
        self.client.force_login(self.staff)
        return self.client.get(reverse("interaction_review"), params)

    def test_a_claim_is_one_card_with_every_source_and_links_to_them(self):
        self.proposal(doi="10.1000/a", score=0.6)
        self.proposal(doi="10.1000/b", score=0.9)
        response = self.get()
        self.assertEqual(response.content.decode().count('data-testid="claim"'), 1)
        self.assertContains(response, "2 sources")
        self.assertContains(response, "best score 0.90")
        self.assertContains(response, "Paper 10.1000/a")
        self.assertContains(response, 'href="https://doi.org/10.1000/b"')
        self.assertContains(response, 'rel="noopener noreferrer nofollow"')
        self.assertContains(response, "Smith J, Jones K, Lee A")
        self.assertContains(response, "Open access")

    def test_the_sentence_is_shown_with_the_two_names_highlighted(self):
        self.proposal()
        html = self.get().content.decode()
        self.assertIn("<mark>Ips typographus</mark> carries <mark>Ophiostoma polonicum</mark>", html)

    def test_evidence_and_titles_cannot_inject_html(self):
        self.proposal(evidence="Ips typographus <script>alert(1)</script> Ophiostoma polonicum", source_title="<img src=x onerror=alert(2)>")
        html = self.get().content.decode()
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_a_source_link_is_only_ever_a_web_link(self):
        self.proposal(source_url="javascript:alert(1)", source_doi="")
        html = self.get().content.decode()
        self.assertNotIn("javascript:alert", html)

    def test_best_scoring_claims_come_first(self):
        self.proposal("Low fungus", doi="10.1/low", score=0.3)
        self.proposal("High fungus", doi="10.1/high", score=0.95)
        html = self.get().content.decode()
        self.assertLess(html.index("High fungus"), html.index("Low fungus"))

    def test_filters(self):
        self.proposal("Ophiostoma polonicum", doi="10.1/a", score=0.9)
        self.proposal("Quercus robur", doi="10.1/b", score=0.4, category="Host plant")
        self.proposal("Raffaelea lauricola", doi="10.1/c", score=0.8, beetle=self.affinis)
        self.assertNotContains(self.get(category="Fungi"), "Quercus robur")
        self.assertNotContains(self.get(min_score="0.5"), "Quercus robur")
        self.assertContains(self.get(min_score="0.5"), "Ophiostoma polonicum")
        self.assertContains(self.get(q="affinis"), "Raffaelea lauricola")
        self.assertNotContains(self.get(q="affinis"), "Ophiostoma polonicum")
        self.assertContains(self.get(q="quercus"), "Quercus robur")
        self.assertContains(self.get(min_score="not a number"), "Quercus robur")  # a bad value is ignored, not an error

    def test_tabs_show_counts_and_the_status_asked_for(self):
        self.proposal(doi="10.1/a")
        self.proposal("Beauveria bassiana", doi="10.1/b", status="rejected")
        response = self.get()
        self.assertContains(response, "Waiting <span")
        self.assertContains(response, "(1)")
        self.assertNotContains(response, "Beauveria bassiana")
        self.assertContains(self.get(status="rejected"), "Beauveria bassiana")
        self.assertContains(self.get(status="nonsense"), "Ophiostoma polonicum")

    def test_the_dataset_is_checked_for_the_same_claim(self):
        PathogenInteraction.objects.create(beetle_host="Ips typographus", beetle_host_id="1733", pathogen="Ophiostoma polonicum",
                                           category="Fungi", record_number="12", source="Smith, 2001")
        PathogenInteraction.objects.create(beetle_host="Ips typographus", beetle_host_id="1733", pathogen="Ophiostoma canum", category="Fungi")
        self.proposal("Ophiostoma polonicum", doi="10.1/a")
        self.proposal("Ophiostoma bicolor", doi="10.1/b")
        self.proposal("Nothing related", doi="10.1/c")
        response = self.get()
        self.assertContains(response, "Already in the published dataset (record 12")
        self.assertContains(response, "same genus")
        self.assertContains(response, "Already in the dataset</button>", count=1)

    def test_query_count_does_not_grow_with_the_number_of_claims(self):
        def queries():
            self.get()  # a first request also pays one-off costs (session, content types)
            with CaptureQueriesContext(connection) as ctx:
                self.get()
            return len(ctx)
        for n in range(2):
            for k in range(2):
                self.proposal(f"Partner {n}{k}", doi=f"10.1/{n}{k}")
        few = queries()
        for n in range(15):
            self.proposal(f"More {n}", doi=f"10.2/{n}", beetle=self.affinis)
            self.proposal(f"More {n}", doi=f"10.3/{n}", beetle=self.affinis)
        self.assertEqual(few, queries())

    def test_it_pages(self):
        for n in range(review_page_size() + 3):
            self.proposal(f"Fungus {n:03d}", doi=f"10.1/{n}", score=1 - n / 1000)
        first, second = self.get(), self.get(page=2)
        self.assertContains(first, "Page 1 of 2")
        self.assertContains(second, "Fungus 020")
        self.assertNotContains(second, "Fungus 000")

    def test_an_empty_queue_hides_the_filters_and_explains_itself_with_a_link(self):
        response = self.get()
        self.assertContains(response, "No claims waiting.")
        self.assertContains(response, reverse("upload_interaction_proposals"))
        self.assertNotContains(response, 'id="f-score"')   # the filter form itself is hidden
        self.assertContains(self.get(status="accepted"), "No accepted claims yet.")
        self.assertContains(self.get(status="rejected"), "No rejected claims yet.")

    def test_filtering_to_nothing_keeps_the_filters_and_says_so_plainly(self):
        self.proposal("Ophiostoma polonicum", doi="10.1/a")
        response = self.get(q="no such beetle")
        self.assertContains(response, 'id="f-score"')   # the queue has claims, so the filters stay
        self.assertContains(response, "No claims match these filters.")
        self.assertNotContains(response, "No claims waiting.")

    def test_the_score_field_has_a_range_hint(self):
        self.proposal()
        self.assertContains(self.get(), "0&ndash;1")


def review_page_size():
    from beetlesgallery.beetles_app.interaction_views import PAGE_SIZE
    return PAGE_SIZE


class DecisionTests(ReviewCase):
    def test_accepting_publishes_one_cited_row_and_links_every_source(self):
        a = self.proposal(doi="10.1000/a", score=0.6, source_authors="Andrei AM, Lupastean D", source_year=2013)
        b = self.proposal(doi="10.1000/b", score=0.9, source_authors="Smith J, Jones K, Lee A", source_year=2020)
        response = self.decide(self.key(), "accept", note="Checked the paper")
        self.assertContains(response, "Accepted and published (2 sources)")
        row = PathogenInteraction.objects.get()
        self.assertEqual((row.origin, row.added_by, row.beetle_host, row.beetle_host_id), ("proposal", self.staff, "Ips typographus", "1733"))
        self.assertEqual((row.pathogen, row.category, row.ecological_relationship), ("Ophiostoma polonicum", "Fungi", "associate"))
        self.assertEqual((row.source, row.year, row.title), ("Smith et al., 2020", "2020", "Paper 10.1000/b"))  # the best-scoring source
        self.assertEqual((row.doi_or_full_text, row.full_text_status), ("https://doi.org/10.1000/b", "abstract only"))
        for p in (a, b):
            p.refresh_from_db()
            self.assertEqual((p.status, p.published_as, p.reviewed_by, p.review_note), ("accepted", row, self.staff, "Checked the paper"))
            self.assertIsNotNone(p.reviewed_at)

    def test_the_reviewer_can_correct_what_is_published(self):
        p = self.proposal("Raffaelea sp.", category="", relationship="")
        self.decide(self.key("Raffaelea sp."), "accept", partner_name="Raffaelea lauricola", category="Fungi", relationship="symbiont")
        row = PathogenInteraction.objects.get()
        self.assertEqual((row.pathogen, row.category, row.ecological_relationship), ("Raffaelea lauricola", "Fungi", "symbiont"))
        p.refresh_from_db()
        self.assertEqual(p.partner_name, "Raffaelea sp.")  # what was proposed is kept as it was

    def test_a_claim_can_only_be_decided_once(self):
        self.proposal()
        self.decide(self.key(), "accept")
        response = self.decide(self.key(), "accept")
        self.assertContains(response, "already decided")
        self.assertEqual(PathogenInteraction.objects.count(), 1)
        self.assertContains(self.decide(self.key(), "reject"), "already decided")

    def test_rejecting_keeps_the_claim_and_reopening_puts_it_back(self):
        p = self.proposal()
        self.assertContains(self.decide(self.key(), "reject", note="Wrong beetle"), "Rejected")
        p.refresh_from_db()
        self.assertEqual((p.status, p.review_note, p.reviewed_by), ("rejected", "Wrong beetle", self.staff))
        self.assertFalse(PathogenInteraction.objects.exists())
        self.assertContains(self.decide(self.key(), "reopen"), "waiting list")
        p.refresh_from_db()
        self.assertEqual((p.status, p.review_note, p.reviewed_by), ("proposed", "", None))

    def test_a_claim_in_the_dataset_can_be_marked_covered_without_publishing_another_row(self):
        existing = PathogenInteraction.objects.create(beetle_host="Ips typographus", beetle_host_id="1733",
                                                      pathogen="ophiostoma polonicum", category="Fungi", record_number="12")
        p = self.proposal()
        self.assertContains(self.decide(self.key(), "covered"), "already in the dataset")
        p.refresh_from_db()
        self.assertEqual((p.status, p.published_as), ("accepted", existing))
        self.assertEqual(PathogenInteraction.objects.count(), 1)

    def test_covered_needs_the_dataset_to_have_it(self):
        self.proposal()
        self.assertContains(self.decide(self.key(), "covered"), "no record of that beetle and partner")

    def test_bad_input_changes_nothing(self):
        p = self.proposal()
        self.assertContains(self.decide(self.key(), "delete"), "Choose accept")
        self.assertContains(self.decide("garbage", "accept"), "not recognised")
        self.assertContains(self.decide(self.key(), "accept", category="x" * 65), "64")
        self.assertContains(self.decide(self.key(), "accept", partner_name="x" * 256), "255")
        p.refresh_from_db()
        self.assertEqual(p.status, "proposed")
        self.assertFalse(PathogenInteraction.objects.exists())

    def test_a_decision_takes_effect_only_for_that_claim_and_that_state(self):
        keep = self.proposal("Other fungus", doi="10.1/o")
        self.proposal()
        self.decide(self.key(), "accept")
        keep.refresh_from_db()
        self.assertEqual(keep.status, "proposed")

    def test_the_page_returns_to_the_filters_it_was_on(self):
        self.proposal()
        self.client.force_login(self.staff)
        response = self.client.post(reverse("interaction_review"), {"claim": self.key(), "decision": "reject", "status": "proposed", "q": "ips", "page": "2"})
        self.assertRedirects(response, reverse("interaction_review") + "?status=proposed&q=ips&page=2", fetch_redirect_response=False)


class CitationTests(ReviewCase):
    def test_citation_styles(self):
        cite = review.citation_for
        self.assertEqual(cite("Andrei AM, Lupăştean D, Ciornei C.", 2013), "Andrei et al., 2013")
        self.assertEqual(cite("Ashraf M, Berryman AA", 1970), "Ashraf and Berryman, 1970")
        self.assertEqual(cite("Smith J.", 2001), "Smith, 2001")
        self.assertEqual(cite("Van der Berg PJ, Lee A", 2020), "Van der Berg and Lee, 2020")
        self.assertEqual(cite("", 2020, journal="J Beetles"), "J Beetles, 2020")
        self.assertEqual(cite("", None, title="A title"), "A title")
        self.assertEqual(cite("", None), "")

    def test_highlighting_escapes_everything_else(self):
        html = review.highlight("A <b>Ips typographus</b> & ips", ["Ips typographus", "ips"])
        self.assertEqual(html, "A &lt;b&gt;<mark>Ips typographus</mark>&lt;/b&gt; &amp; <mark>ips</mark>")
        self.assertEqual(review.highlight("<i>", []), "&lt;i&gt;")


class AdditionsTests(ReviewCase):
    def test_accepted_claims_reach_the_interactions_page_next_to_the_dataset(self):
        PathogenInteraction.objects.create(beetle_host="Ips typographus", pathogen="Beauveria", category="Fungi", record_number="1")  # dataset
        self.proposal()
        self.assertEqual(len(self.client.get(reverse("interactions_records")).json()), 1)
        self.decide(self.key(), "accept")
        self.client.logout()
        records = self.client.get(reverse("interactions_records")).json()
        self.assertEqual([r["origin"] for r in records], ["dataset", "proposal"])
        record = records[1]
        self.assertEqual((record["Beetle Host"], record["pathogens"], record["categories"]),
                         ("Ips typographus", "Ophiostoma polonicum", "Fungi"))
        self.assertEqual((record["year"], record["source"]), (2020, "Smith et al., 2020"))
        self.assertEqual(record["doi or full text"], "https://doi.org/10.1000/a")

    def test_the_interactions_page_reads_everything_from_the_database(self):
        response = self.client.get(reverse("interactions_preview"))
        for name in ("interactions_records", "interactions_hosts", "interactions_references"):
            self.assertContains(response, reverse(name))
        self.assertNotContains(response, "bark_beetle_pathogens_master.json")

    def test_interactions_page_shows_the_review_links_and_number_waiting_to_staff_only(self):
        self.proposal()
        self.proposal("Beauveria bassiana", doi="10.1/b")
        self.client.force_login(self.staff)
        page = self.client.get(reverse("interactions_preview"))
        self.assertContains(page, "2 waiting")
        self.assertContains(page, "Upload or Update Interactions")
        self.assertNotContains(self.client.get(reverse("data_management")), "Review Proposed Interactions")
        self.client.force_login(self.user)
        page = self.client.get(reverse("interactions_preview"))
        self.assertNotContains(page, "Review Proposed Interactions")
        self.assertNotContains(page, "Upload or Update Interactions")
