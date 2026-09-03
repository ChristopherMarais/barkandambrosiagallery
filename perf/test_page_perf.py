"""
Page performance regression tests (pass/fail against a time budget).

Isolated from the app: reuses the seeding helpers from scaling_probe, seeds a
fixed synthetic dataset, and asserts each page renders under a millisecond
budget and without a query-count blow-up. All inside a transaction that is
rolled back - the dev database is left untouched.

Run:
    docker compose run --rm web pixi run python -m unittest perf.test_page_perf -v

Tune BUDGET_MS / QUERY_CEILING to your machine; treat these as "don't regress"
guards, not absolute targets. Numbers are wall-clock in the container.
"""
import os
import statistics
import time

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "beetlesgallery.settings")
django.setup()

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.test import Client

from perf.scaling_probe import seed_taxa, seed_specimens, _reset_perf

# Fixed synthetic dataset for the test (added on top of whatever is already there)
SEED_TAXA = 5000
SEED_SPECIMENS = 2000
REPEATS = 4

# Budgets (median wall-clock ms) and hard query ceilings per request.
BUDGET_MS = {
    "/taxonomy/": 1500,
    "/beetles/?per_page=12": 1200,
    "/tools/annotate/": 1200,
    "/api/v1/species/?page_size=10000": 1500,
    "/api/v1/beetles/images-with-annotations/?page=1&page_size=50": 900,
}
QUERY_CEILING = {
    "/taxonomy/": 15,
    "/beetles/?per_page=12": 80,
    "/tools/annotate/": 80,
    "/api/v1/species/?page_size=10000": 15,
    "/api/v1/beetles/images-with-annotations/?page=1&page_size=50": 150,
}


class PagePerfTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        _reset_perf()
        seed_taxa(SEED_TAXA, 0)
        taxon_ids = list(
            __import__("beetlesgallery.beetles_app.models", fromlist=["Taxon"])
            .Taxon.objects.filter(valid_species_id__startswith="PERF-")
            .values_list("id", flat=True)
        )
        seed_specimens(SEED_SPECIMENS, 0, taxon_ids)

    def setUp(self):
        self.client = Client()
        user = get_user_model().objects.filter(is_superuser=True).first()
        assert user, "no superuser - run createsuperuser"
        self.client.force_login(user)

    def _profile(self, url):
        self.client.get(url, HTTP_HOST="localhost")  # warm
        times, qcounts = [], []
        for _ in range(REPEATS):
            with CaptureQueriesContext(connection) as ctx:
                t0 = time.perf_counter()
                resp = self.client.get(url, HTTP_HOST="localhost")
                times.append((time.perf_counter() - t0) * 1000)
            qcounts.append(len(ctx.captured_queries))
        return resp.status_code, statistics.median(times), max(qcounts)

    def test_pages_within_budget(self):
        rows = []
        failures = []
        for url, budget in BUDGET_MS.items():
            code, ms, q = self._profile(url)
            rows.append((url, code, ms, q))
            if code != 200:
                failures.append(f"{url}: HTTP {code}")
            if ms > budget:
                failures.append(f"{url}: {ms:.0f} ms > budget {budget} ms")
            if q > QUERY_CEILING[url]:
                failures.append(f"{url}: {q} queries > ceiling {QUERY_CEILING[url]}")

        print(f"\n  (seeded +{SEED_TAXA} taxa, +{SEED_SPECIMENS} specimens)\n")
        print(f"  {'url':<58} {'code':>4} {'ms':>8} {'queries':>8}")
        for url, code, ms, q in rows:
            print(f"  {url:<58} {code:>4} {ms:>8.0f} {q:>8}")
        print()

        self.assertEqual(failures, [], "\n  - " + "\n  - ".join(failures) if failures else "")
