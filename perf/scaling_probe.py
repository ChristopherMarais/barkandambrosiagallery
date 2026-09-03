#!/usr/bin/env python
"""
Standalone page-performance / scaling probe.

Measures wall-clock render time, SQL query count, and DB time for the main
pages at several dataset sizes, then estimates the empirical time complexity
(the exponent b in  time ~ n**b) from a log-log fit.

It does NOT modify the codebase and does NOT keep any data: everything it
inserts is done inside one transaction that is rolled back at the end. A hard
kill mid-run is also safe (Postgres discards the uncommitted transaction).

Run inside the web container:

    docker compose run --rm web pixi run python perf/scaling_probe.py
    docker compose run --rm web pixi run python perf/scaling_probe.py --quick
    docker compose run --rm web pixi run python perf/scaling_probe.py --cleanup   # paranoia sweep

Options:
    --quick        smaller sizes / fewer repeats (fast smoke run)
    --repeats N     timed requests per data point (default 5, median reported)
    --only NAME     run one target only (taxonomy|gallery|annotate|api_species|api_images)
    --cleanup       delete any leftover rows tagged 'PERF-' / 'perf/' and exit
"""
import argparse
import os
import statistics
import sys
import time

import django

# make the repo root importable no matter where this script is launched from
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "beetlesgallery.settings")
django.setup()

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext

from beetlesgallery.beetles_app.models import Beetles, ImageAsset, Taxon

# --- treebeard path helper (copied from migrate_taxonomy_to_db) -----------------
_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def _mp_path(num, steplen=4):
    base = len(_ALPHABET)
    res = []
    while num > 0:
        num, rem = divmod(num, base)
        res.append(_ALPHABET[rem])
    return "".join(reversed(res)).rjust(steplen, _ALPHABET[0])


PERF_TAXON_PREFIX = "PERF-"
PERF_IMG_PREFIX = "perf/"
_PATH_OFFSET = 10_000_000  # fixed; keeps probe mp-paths clear of real 4-char paths


# --- seeding ------------------------------------------------------------------
def seed_taxa(n, made_so_far):
    """Create n more depth-1 Taxon rows with a realistic subfamily/tribe/genus spread."""
    start = made_so_far
    offset = _PATH_OFFSET
    rows = []
    for i in range(start, start + n):
        rows.append(Taxon(
            path=_mp_path(offset + i, steplen=6),
            depth=1,
            numchild=0,
            valid_species_id=f"{PERF_TAXON_PREFIX}{i}",
            scientific_name=f"Perfus specimen{i}",
            scientific_name_authority="Probe, 2026",
            subfamily=f"Perfinae{i % 2}",          # 2 subfamilies
            tribe=f"Perfini{i % 25}",              # ~25 tribes
            subtribe="",
            genus=f"Perfus{i % max(1, n // 6)}",   # ~n/6 genera (mirrors real ratio)
            species=f"specimen{i}",
            subspecies="",
            authority="Probe",
            authority_year="2026",
            original_genus="",
        ))
    Taxon.objects.bulk_create(rows, batch_size=5000)


def seed_specimens(n, made_so_far, taxon_ids):
    """Create n more ImageAsset+Beetles pairs. Distinct-value fields grow with n
    so the filter-dropdown queries in gallery/annotate scale realistically."""
    start = made_so_far
    imgs = [
        ImageAsset(
            full_path_at_import=f"{PERF_IMG_PREFIX}img_{i}.jpg",
            image_institution=f"Inst {i % max(1, n // 20)}",
            photographer=f"Photographer {i % max(1, n // 15)}",
            is_validated=(i % 3 == 0),
        )
        for i in range(start, start + n)
    ]
    ImageAsset.objects.bulk_create(imgs, batch_size=5000)

    tcount = len(taxon_ids)
    countries = max(1, n // 12)
    beetles = [
        Beetles(
            image_asset=imgs[k],
            taxon_id=taxon_ids[(start + k) % tcount] if tcount else None,
            depicts_valid_name_id=f"{PERF_TAXON_PREFIX}{(start + k) % tcount}" if tcount else None,
            depicts_specimen=f"specimen_{(start + k) % max(1, n // 4)}",
            collection_country=f"Country {(start + k) % countries}",
            collection_stateProvince=f"State {(start + k) % max(1, n // 8)}",
            specimen_sex=("female", "male", "")[(start + k) % 3],
            specimen_type_status=("", "holotype", "paratype")[(start + k) % 3],
            aspect=("dorsal", "lateral", "")[(start + k) % 3],
        )
        for k in range(n)
    ]
    Beetles.objects.bulk_create(beetles, batch_size=5000)


# --- measurement -------------------------------------------------------------
def measure(client, url, repeats):
    # warm once (template compile, etc.)
    client.get(url, HTTP_HOST="localhost")
    times, qcounts, qsecs, status = [], [], [], None
    for _ in range(repeats):
        with CaptureQueriesContext(connection) as ctx:
            t0 = time.perf_counter()
            resp = client.get(url, HTTP_HOST="localhost")
            dt = time.perf_counter() - t0
        status = resp.status_code
        times.append(dt * 1000.0)
        qcounts.append(len(ctx.captured_queries))
        qsecs.append(sum(float(q["time"]) for q in ctx.captured_queries) * 1000.0)
    return {
        "status": status,
        "ms_median": statistics.median(times),
        "ms_p95": max(times),
        "queries": statistics.median(qcounts),
        "db_ms_median": statistics.median(qsecs),
    }


def fit_exponent(xs, ys):
    """Least-squares slope of log(y) vs log(x) => exponent b in y ~ x**b, plus R²."""
    pts = [(x, y) for x, y in zip(xs, ys) if x > 0 and y > 0]
    if len(pts) < 2:
        return None, None
    import math
    lx = [math.log(x) for x, _ in pts]
    ly = [math.log(y) for _, y in pts]
    n = len(pts)
    mx, my = sum(lx) / n, sum(ly) / n
    sxx = sum((v - mx) ** 2 for v in lx)
    sxy = sum((a - mx) * (b - my) for a, b in zip(lx, ly))
    if sxx == 0:
        return None, None
    b = sxy / sxx
    a = my - b * mx
    ss_res = sum((yv - (a + b * xv)) ** 2 for xv, yv in zip(lx, ly))
    ss_tot = sum((yv - my) ** 2 for yv in ly)
    r2 = 1 - ss_res / ss_tot if ss_tot else 1.0
    return b, r2


def label_complexity(b):
    if b is None:
        return "?"
    if b < 0.25:
        return "~O(1)  (flat / cached)"
    if b < 0.7:
        return "sub-linear"
    if b < 1.3:
        return "~O(n)  (linear)"
    if b < 1.7:
        return "~O(n log n)"
    if b < 2.4:
        return "~O(n^2)  (quadratic - investigate)"
    return f"~O(n^{b:.1f})  (super-quadratic - investigate)"


# --- targets ---------------------------------------------------------------
TARGETS = {
    "taxonomy": {
        "url": "/taxonomy/",
        "scale": "taxa",
        "sizes": [500, 2000, 5000, 12000, 25000],
        "sizes_quick": [500, 2000, 6000],
    },
    "gallery": {
        "url": "/beetles/?per_page=12",
        "scale": "specimens",
        "sizes": [200, 1000, 4000, 12000],
        "sizes_quick": [100, 500, 2000],
    },
    "annotate": {
        "url": "/tools/annotate/",
        "scale": "specimens",
        "sizes": [200, 1000, 4000, 12000],
        "sizes_quick": [100, 500, 2000],
    },
    "api_species": {
        "url": "/api/v1/species/?page_size=10000",
        "scale": "taxa",
        "sizes": [500, 2000, 5000, 12000, 25000],
        "sizes_quick": [500, 2000, 6000],
    },
    "api_images": {
        "url": "/api/v1/beetles/images-with-annotations/?page=1&page_size=50",
        "scale": "specimens",
        "sizes": [200, 1000, 4000, 12000],
        "sizes_quick": [100, 500, 2000],
    },
}


def _reset_perf(quiet=True):
    """Remove all probe-inserted rows so each target starts from the real baseline."""
    Beetles.objects.filter(depicts_valid_name_id__startswith=PERF_TAXON_PREFIX).delete()
    Beetles.objects.filter(image_asset__full_path_at_import__startswith=PERF_IMG_PREFIX).delete()
    ImageAsset.objects.filter(full_path_at_import__startswith=PERF_IMG_PREFIX).delete()
    Taxon.objects.filter(valid_species_id__startswith=PERF_TAXON_PREFIX).delete()


def cleanup():
    b = Beetles.objects.filter(image_asset__full_path_at_import__startswith=PERF_IMG_PREFIX).delete()
    i = ImageAsset.objects.filter(full_path_at_import__startswith=PERF_IMG_PREFIX).delete()
    t = Taxon.objects.filter(valid_species_id__startswith=PERF_TAXON_PREFIX).delete()
    print(f"cleanup: beetles={b} imageassets={i} taxa={t}")


def run(args):
    User = get_user_model()
    user = User.objects.filter(is_superuser=True).order_by("id").first()
    if not user:
        sys.exit("No superuser found - create one first.")
    client = Client()
    client.force_login(user)

    names = [args.only] if args.only else list(TARGETS)
    base_taxa = Taxon.objects.count()
    base_specimens = Beetles.objects.count()
    print(f"baseline: {base_taxa} taxa, {base_specimens} specimens | repeats={args.repeats}\n")

    for name in names:
        cfg = TARGETS[name]
        sizes = cfg["sizes_quick"] if args.quick else cfg["sizes"]
        scale = cfg["scale"]
        print("=" * 78)
        print(f"{name}   {cfg['url']}   (scaling with # {scale})")
        print("-" * 78)
        print(f"{scale+' rows':>12} {'status':>7} {'ms (med)':>10} {'ms (p95)':>10} "
              f"{'queries':>8} {'db ms':>9}")

        _reset_perf()  # independent baseline per target
        xs, ys, qs, made = [], [], [], 0
        taxon_ids = []
        for target_total in sizes:
            need = target_total - made
            if need > 0:
                if scale == "taxa":
                    seed_taxa(need, made)
                    taxon_ids = list(Taxon.objects.filter(
                        valid_species_id__startswith=PERF_TAXON_PREFIX
                    ).values_list("id", flat=True))
                else:
                    if not taxon_ids:
                        seed_taxa(200, 0)
                        taxon_ids = list(Taxon.objects.filter(
                            valid_species_id__startswith=PERF_TAXON_PREFIX
                        ).values_list("id", flat=True))
                    seed_specimens(need, made, taxon_ids)
                made = target_total

            total_now = (base_taxa if scale == "taxa" else base_specimens) + made
            r = measure(client, cfg["url"], args.repeats)
            print(f"{total_now:>12,} {r['status']:>7} {r['ms_median']:>10.1f} "
                  f"{r['ms_p95']:>10.1f} {r['queries']:>8.0f} {r['db_ms_median']:>9.1f}")
            xs.append(total_now)
            ys.append(r["ms_median"])
            qs.append(r["queries"])

        b, r2 = fit_exponent(xs, ys)
        if b is not None:
            print(f"\n  time vs {scale}:   exponent b = {b:.2f}  (R^2={r2:.2f})  ->  {label_complexity(b)}")
        if len(set(qs)) > 1:
            qb, qr2 = fit_exponent(xs, qs)
            print(f"  queries vs {scale}: exponent b = {qb:.2f}  (R^2={qr2:.2f})  ->  "
                  f"{'query count grows with data - N+1 smell' if qb and qb > 0.3 else 'roughly constant'}")
        else:
            print(f"  queries: constant at {qs[0]:.0f} (good)")
        print()

    print("rolling back all probe data ...")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--only", choices=list(TARGETS))
    ap.add_argument("--cleanup", action="store_true")
    args = ap.parse_args()

    if args.cleanup:
        cleanup()
        return

    try:
        with transaction.atomic():
            run(args)
            transaction.set_rollback(True)  # discard everything the probe inserted
    finally:
        # belt-and-suspenders: if anything committed unexpectedly, sweep it
        leftover = Taxon.objects.filter(valid_species_id__startswith=PERF_TAXON_PREFIX).exists()
        if leftover:
            print("WARNING: probe rows survived the rollback - running cleanup()")
            cleanup()
    print("done.")


if __name__ == "__main__":
    main()
