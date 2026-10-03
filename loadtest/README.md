# Load test (issue #383)

Simulated players play the Beetle ID game against a site, so we can see how it copes before inviting a club of ~40.
Each one signs in as a throw-away `loadtest-NN` account and plays like a person: opens the photos, thinks for 3-10 s,
answers cautiously (or skips), and now and then opens the leaderboard or the expertise tree.

[Locust](https://locust.io) runs the players. It is not part of the site's dependencies; install it where you run the test:

```bash
pipx install locust        # or: pip install locust
```

## Before: on the server

```bash
cd /opt/barkandambrosiagallery
read -rs LOADTEST_PASSWORD && export LOADTEST_PASSWORD     # type a long password; it is never shown or stored
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T -e LOADTEST_PASSWORD web \
  pixi run python manage.py loadtest_players --create 50
```

Then switch on the banner on **My account -> Site notice** ("We're stress-testing the site ...").

## Run: from a laptop (not the server, so the test doesn't compete with the site)

```bash
read -rs LOADTEST_PASSWORD && export LOADTEST_PASSWORD     # the same password
export SITE=https://barkandambrosiagallery.org
mkdir -p results

# 1. Ramp to 50 players over ~25 s and hold for 15 minutes
locust -f loadtest/locustfile.py --host "$SITE" --headless -u 50 -r 2 -t 17m --csv results/idle-50
# 2. Spike to 100 for 5 minutes
locust -f loadtest/locustfile.py --host "$SITE" --headless -u 100 -r 10 -t 5m --csv results/idle-100
```

(`LOADTEST_PLAYERS=100` before the spike so all 100 have their own account: create 100 on the server in that case.)

**Second run, with a heavy job going:** start a big CSV upload or update (or a downloads build) on the site, then
repeat step 1 with `--csv results/heavy-50`. Uploads and updates now run on their own low-priority worker, so this
shows whether that keeps the game quick.

### Watch while it runs (on the server)

```bash
docker stats --no-stream                                    # CPU and memory per container
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec redis redis-cli llen celery   # quick jobs waiting
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec redis redis-cli llen heavy    # heavy jobs waiting
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec db psql -U beetles_user -d beetles_db \
  -c "select count(*) from pg_stat_activity"                # open database connections
```

## Targets

| What | Target |
|---|---|
| `game: answer` and `game: start`, 95th percentile | under 500 ms |
| Beetle photos, 95th percentile | under 300 ms |
| Errors | none |

Locust prints a table per request at the end; the CSVs in `results/` hold the same numbers. Paste the
`*_stats.csv` summary rows into issue #383.

## After: clean up (always)

Switch off the site notice, then on the server:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T web \
  pixi run python manage.py loadtest_players --delete
```

This deletes every `loadtest-*` account with all their answers and rounds, then re-scores the real players, so
nothing the test players said is left in anyone's consensus or points. While the test runs they do show on the
leaderboard; the banner explains why.
