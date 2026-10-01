# Releasing v2.0.0 (from v1.3.2)

Work top to bottom and tick each box. If a box fails, note what you saw in the **Patch log** at the end, and
see **If something breaks** before patching.

## What changes for production

- **14 new migrations** (`0018` to `0031`). All only add tables and columns; nothing existing is altered or
  dropped (the deploy-safety tests enforce this). v1.3.2's code still runs on the new database, so a code
  rollback doesn't need a database restore.
- **New features:** the game (entirely new: there is no game data in production yet), access requests with email
  verification and approver reminders, the interactions page (seeded on every deploy by `import-interactions`),
  model predictions, game proposals and reports on Image Annotation.
- **New environment variables** in `.env.prod` (all have defaults, but email needs real values):

  | Variable | Needed? | Default |
  |---|---|---|
  | `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` | **Yes.** Without `EMAIL_HOST`, emails are only printed to the log, so nobody can verify a request or reset a password. | none |
  | `DEFAULT_FROM_EMAIL` | Recommended | `EMAIL_HOST_USER` or noreply@barkandambrosiagallery.org |
  | `ACCESS_REQUEST_RECIPIENTS` | Recommended (comma-separated approvers) | gmarais@ufl.edu |
  | `SITE_URL` | Only if the domain differs (used in emails and the game's QR code) | https://barkandambrosiagallery.org |
  | `GAME_RECOMPUTE_IN_BACKGROUND` | No (on in production) | 1 |

- **New scheduled workflows** (they use the same `SERVER_IP` / `SERVER_USER` / `SSH_PRIVATE_KEY` secrets as
  the deploy): `game-scores.yml` (nightly 03:23 UTC) and `access-reminders.yml` (daily 14:17 UTC).
- **Removed:** the `build-tree` pixi task. If anything on the server (crontab, script) still runs
  `pixi run build-tree`, remove it.

## Before you release

- [ ] **Back up the database.** Run the *Dropbox Backup* workflow by hand (Actions → Dropbox Backup → Run workflow) and confirm
      the dump exists and isn't empty. Note its file name here: `__________`
- [ ] **CI is green on `main`** (Actions → Tests, latest run on `6ec178d` or later).
- [ ] **`.env.prod` on the server has the email variables** (table above). Send a test email after the deploy
      (see "Email").
- [ ] **Server disk space**: `df -h` on the server shows a few GB free (the build makes new images).
- [ ] **Pick a quiet time**: the deploy restarts the web container (a few seconds of errors).

## Release

- [ ] On GitHub: Releases → *Draft a new release* → tag **`v2.0.0`** on `main` → title "v2.0.0" → Publish.
      Publishing starts the *Deploy App* workflow.
- [ ] Watch Actions → Deploy App. Every step should pass. Check the log for:
  - [ ] `migrate`: applies `0018` ... `0031` with no errors.
  - [ ] `import-interactions`: reports rows added (first time) or nothing to add.
  - [ ] `collectstatic`: no errors.
- [ ] On the server: `docker compose -f docker-compose.yml -f docker-compose.prod.yml ps` shows `web`,
      `celery` (worker), `redis` and the database **Up**.
- [ ] The footer of the site shows **v2.0.0**.

## Smoke test (5 minutes, signed out)

- [ ] Home page loads; the game panel shows a **Beta** pill.
- [ ] Image Browser: search, filters, open an image, the detail page loads with the image.
- [ ] Taxonomy Browser loads and expands.
- [ ] Interactions page loads with data and charts.
- [ ] AI Identification: upload an image, get a result.
- [ ] Downloads still require an account.
- [ ] A phone (or narrow browser window): the sidebar opens and closes, pages don't scroll sideways.

## Accounts and email

- [ ] **Email**: on the server run
      `docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T web pixi run python manage.py send_test_email you@example.com`
      and receive it (check spam).
- [ ] **Request access** with a new address: the verification email arrives, the link verifies it.
- [ ] The approver(s) in `ACCESS_REQUEST_RECIPIENTS` get the "new request" email.
- [ ] As a superuser, approve the request; the new account can sign in.
- [ ] **Password reset**: "Forgot password" sends an email and the link lets you set a new password.
- [ ] Sign in with an existing (pre-v2) account: it still works and keeps its access.

## Image Annotation (staff)

- [ ] The page loads; the image list loads and scrolls.
- [ ] Open an image, edit a box, save, validate an ROI: it saves and the lock works (open it in a second browser
      as another user: it says who is editing).
- [ ] Advanced filters → *Game label proposals*: *With game proposals*, *Expert-backed only*, *Reported by
      players* and the *most confident first* sort all load (they'll be empty until people play).
- [ ] Upload / update (Data Management) still works with a small test file.

## The game, as a new player (use a test account)

- [ ] Sidebar: below the divider, above Logout, your username, a grey **1** badge and "0 pts". It links to the game.
- [ ] Game home: **Beta** pill, big **Play** button, level card, streak, today, accuracy panel ("N more answers
      ..."), links, top players, and a large **QR code** that scans to `https://barkandambrosiagallery.org/game/`.
- [ ] Play: the first beetles are all **Similarity** (level 1). The toolbar shows Both and Identification locked,
      and Focus locked. Tapping a lock says which level opens it.
- [ ] The top bar shows today's count, "L1 · 0/50" and a bar under the header that grows as you answer.
- [ ] Answer about 10: no errors, the next beetle comes straight away, "Seen before" / "Named by others" tags show
      when they apply.
- [ ] Tap a photo: the full image opens; the faint cog bottom-left opens *Report this image* (four one-line
      options). Report one: "Thanks!", and you move on.
- [ ] **Exit**: the recap shows points and any new badges; the game home shows the new score.
- [ ] Leaderboard: you appear; search, sorts, "This week", the specialists filter; names open profiles; the
      dropdown arrows sit inside their boxes.
- [ ] Profile: level badge, stats, badges (earned in colour), back link goes to the game home.
- [ ] Unlocks, Expertise and How it works pages load.

## The game, as a superuser

- [ ] Game home → *Superuser: manage players' unlocks*: find the test account, tick **Unlock everything**, Save.
- [ ] As the test account: the toolbar now allows Both / Identification / Similarity and Focus; switching applies
      from the next beetle; Identification beetles show the four name pickers and search.
- [ ] Game home → *Staff: review game labels*: back link, *Review labels in Image Annotation* button, the three
      collapsible sections. Your test report is listed; its ROI link opens Image Annotation **on that image with
      the ROI open** in the side panel.
- [ ] On that ROI, resolve the report. The image is back in the game afterwards.
- [ ] Untick the test account's unlocks again.

## Background work

- [ ] Celery: `docker compose ... logs --tail=50 celery` shows the worker ready, and after someone exits the game,
      lines for `recompute_game_players_task` (only when other players answered the same beetles) with no errors.
- [ ] Run the nightly job once by hand: Actions → *Nightly game scores* → Run workflow. It finishes green and its log ends
      with "Re-scored N players ...".
- [ ] Run Actions → *Access request reminders* once by hand: it finishes green.
- [ ] Next morning: both scheduled runs show green in Actions.

## Several players at once

- [ ] Two or three people play at the same time for a few minutes (different accounts, phones and laptops), then
      all tap Exit within a few seconds of each other. No errors; scores on the leaderboard look right.
- [ ] `docker compose ... logs --since=30m web | grep -i -E "error|traceback"` is empty (or only known noise).

## After a day

- [ ] Logs (`docker compose ... logs --since=24h web | grep -i traceback`): no new recurring errors.
- [ ] The server's CPU and memory are normal (`docker stats`).
- [ ] The backup workflow ran on schedule.

## If something breaks

- **A page errors but the rest works**: note it below, patch on a branch, PR, merge, then deploy with Actions →
  Deploy App → *Run workflow* (`web`). No new release is needed for a patch; tag `v2.0.1` when convenient.
- **The site is down after the deploy**: look at `docker compose ... logs --tail=200 web` first. To go back to
  v1.3.2 code, on the server:
  `cd /opt/barkandambrosiagallery && git fetch --tags && git reset --hard v1.3.2 && echo "APP_VERSION=v1.3.2" > .env.version && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build && docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T web pixi run collectstatic --no-input && docker compose -f docker-compose.yml -f docker-compose.prod.yml restart web`.
  Leave the database as it is: the new tables are only extra, and v1.3.2 ignores them. Restore the backup only if
  data itself was damaged. The next deploy (from `main`) brings v2 back.
- **Emails don't arrive**: check the email variables in `.env.prod`, restart `web`, and run `send_test_email`
  again; the log shows the SMTP error.

## Patch log

| What | Where | Fixed in |
|---|---|---|
| | | |
