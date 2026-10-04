# Testing

## Running the tests

All tests, the same way CI runs them:
```bash
docker compose run --rm web pixi run python manage.py test beetlesgallery --noinput
```
One file, one class or one test:
```bash
docker compose run --rm web pixi run python manage.py test beetlesgallery.beetles_app.test_game_rewards --noinput
docker compose run --rm web pixi run python manage.py test beetlesgallery.beetles_app.test_game_rewards.StreakTests.test_name --noinput
```
Add `--parallel 4` to run faster. If a parallel run fails with `cannot pickle 'traceback' object`, a test failed:
run without `--parallel` to see which one.

## What CI checks

`.github/workflows/tests.yml` runs on every pull request and every push to `main`:
- It uses a throw-away Postgres and Redis on GitHub's runner, and no secrets, so it can never touch the server.
- It builds the test database from every migration, so a migration that fails on an empty database fails CI.
- It installs only what `pixi.lock` lists. If your code imports a package, add it to `pixi.toml`. (PyYAML, for
  example, is not there.)

A red CI blocks the merge. Fix the cause; don't skip or delete a test to get green.

## Guards you may run into

`test_deploy_safety.py` fails when:
- a new migration would delete data, unless it is marked `DESTRUCTIVE_OK = "why"`;
- a data migration (`RunPython`) has no `DATA_MIGRATION_REVIEWED = "what it reads and writes"`;
- a workflow calls a command that doesn't exist, or one that purges data;
- a server workflow could load the local-only `docker-compose.override.yml`.

`test_staging.py` checks that the staging scripts and compose file can never write to production.

These are on purpose. Read the message: it says what to do.

## Writing tests

- **New file per topic:** `beetles_app/test_<topic>.py`. That way two open PRs rarely edit the same test file.
- **Use the helpers** in `beetles_app/testing.py`: `make_image`, `make_beetle`, `make_taxon` and `PageBehaviourCase`.
  `PageBehaviourCase` gives signed-in users with the usual roles, and renders templates without `collectstatic`.
- **Test behaviour, not markup.** Give the element a `data-testid="..."` and assert on that, not on CSS classes.
- **Settings:** change a setting for one test with `@override_settings(GAME_ROUND_SIZE=1)`. Don't edit
  `settings.py` defaults to make a test pass.
- **No network:** mock the AI classifier and email; tests must pass offline.
- **Expect permissions:** a page that needs an area should be tested both as someone who has it and as someone who
  doesn't (403 or the sign-in redirect).

## Trying it in a browser

Run the site (`docker compose up -d`) and check:
- the page at <http://localhost:8000>, on a phone-width window as well;
- the email it sends, at <http://localhost:8025> (Mailpit).

After merging, check the change again on the [Staging](Staging) site, which runs on real data.
