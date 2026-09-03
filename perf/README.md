# `perf/` — standalone page performance tooling

Self-contained. Does **not** modify the app. Everything it inserts runs inside a
transaction that is rolled back, so the dev database is left exactly as it was.

Two tools:

| File | Purpose | Run |
|---|---|---|
| `scaling_probe.py` | Measure render time + query count at growing dataset sizes, and estimate empirical time complexity (`time ~ n^b`) | `docker compose run --rm web pixi run python perf/scaling_probe.py` |
| `test_page_perf.py` | Pass/fail regression guard: each page must render under a time budget and query ceiling at a fixed synthetic size | `docker compose run --rm web pixi run python -m unittest perf.test_page_perf -v` |

## scaling_probe.py

```
docker compose run --rm web pixi run python perf/scaling_probe.py            # full
docker compose run --rm web pixi run python perf/scaling_probe.py --quick    # fast
docker compose run --rm web pixi run python perf/scaling_probe.py --only taxonomy
docker compose run --rm web pixi run python perf/scaling_probe.py --repeats 9
docker compose run --rm web pixi run python perf/scaling_probe.py --cleanup  # paranoia sweep
```

Output per target: a table of `dataset rows | status | ms (median) | ms (p95) |
queries | db ms`, then a log-log fit giving the exponent `b`:

- `b ~ 1.0` → linear, cost grows with the dataset (needs pagination / caching)
- `b ~ 0.1` → flat, cost is fixed overhead (fine, or already page-limited)
- `b ~ 2.0` → quadratic, investigate

It also fits the **query count** vs size — a rising exponent there means an N+1.

Targets: `/taxonomy/`, `/beetles/`, `/tools/annotate/`,
`/api/v1/species/?page_size=10000`,
`/api/v1/beetles/images-with-annotations/`.

### Safety

All inserts happen inside one `transaction.atomic()` with
`transaction.set_rollback(True)` at the end. A hard kill mid-run is also safe:
Postgres discards the uncommitted transaction when the connection drops. Probe
rows are tagged (`Taxon.valid_species_id` starts `PERF-`,
`ImageAsset.full_path_at_import` starts `perf/`); `--cleanup` deletes any that
somehow survived.

## test_page_perf.py

A `django.test.TestCase` (transaction rolled back). Seeds `SEED_TAXA` +
`SEED_SPECIMENS` rows, then asserts every URL in `BUDGET_MS` renders under its
budget and under `QUERY_CEILING` queries. Tune those dicts to your machine —
they are "don't regress" guards, not targets.

Run with plain `unittest` (not `manage.py test`) so it uses the existing dev
database and skips test-DB creation. (`manage.py test` currently can't build the
test DB anyway — migration `0011` needs the `pg_trgm` extension that no migration
creates; see `../ONBOARDING_NOTES.md`.)

## What the first run found (sample data, in-container, ~8k taxa / 2k specimens)

| page | median ms | queries | scaling | note |
|---|---:|---:|---|---|
| `/taxonomy/` | ~860 | 4 | **O(n) in taxa** | loads every Taxon, builds whole tree in Python each request, no cache |
| `/api/v1/species/?page_size=10000` | ~1060 | 3 | **O(n) in taxa** | DRF serializes 10k rows one object at a time; the annotate tab calls this on load |
| `/beetles/` | ~825 | **47** | sub-linear time | filter-dropdown options rebuilt with ~1 query per filter per request |
| `/tools/annotate/` | ~720 | 41 | flat | same filter-dropdown pattern |
| `/api/v1/beetles/images-with-annotations/` | ~430 | **109** | flat in total data | ~2 queries per row on a 50-row page — N+1 |

DB time is a small fraction of wall time on every page → the bottleneck is
**Python** (tree building, serialization), not the queries themselves.
