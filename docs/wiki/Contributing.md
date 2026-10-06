# Contributing

Thanks for helping. This page is the checklist; the other pages have the details.

## 1. Start from an issue

- Bugs and features are tracked in [Issues](https://github.com/ChristopherMarais/barkandambrosiagallery/issues).
  Game ideas and bug reports from players are in
  [Discussions](https://github.com/ChristopherMarais/barkandambrosiagallery/discussions/categories/beetle-id-game).
- Comment on the issue before you start on anything large, so two people don't build the same thing.

## 2. Make one small, reviewable change

- **One topic per pull request.** A bug fix and a redesign are two PRs. Small PRs get reviewed and merged quickly;
  large ones collect conflicts.
- **Tests come with the change.** Put new tests in a **new file** (`beetles_app/test_<topic>.py`) where you can, so
  open PRs don't conflict over the same test file. See [Testing](Testing).
- **Migrations only add.** Never drop a column or table in a normal PR. See
  [Git Workflow → Migrations](Git-Workflow#migrations).

## 3. Follow the house style

- **Look:** grey, white and black. Use colour only where it carries meaning (an error, a level, a medal). Use the
  Tailwind classes already used nearby, and rebuild the CSS (`docker compose run --rm web pixi run build-css`) when you add new ones.
- **Words:** player-facing and user-facing text is short and plain. One sentence beats a paragraph; a label beats a
  sentence.
- **Mobile:** every page has to work on a phone. Check at 375 px wide.
- **Code:** match the file you are in: its naming, comment density and idioms. Prefer a small module with one job
  (as `game_levels.py`, `game_rewards.py` and friends do) over growing `views.py`.

## 4. Open the pull request

Describe:
- what it changes;
- why it changes it (`Closes #123`);
- how you tested it.

For anything visible, add a screenshot. CI must be green. See [Git Workflow](Git-Workflow).

## 5. Things only the owner does

- **Merging** pull requests.
- **Deploying:** publishing a release, or running the *Deploy App* workflow.
- Running any workflow or command that touches the **production server** or its data: backups, access reminders,
  game scores, the staging refresh.
- Anything with **secrets**. `.env.prod` lives only on the server and is never committed. Never paste a password or
  key into an issue, PR or chat; compare fingerprints (`md5sum`) instead.

These rules are also in [`CLAUDE.md`](https://github.com/ChristopherMarais/barkandambrosiagallery/blob/main/CLAUDE.md),
which AI coding assistants working on the repo follow.

## Security problems

Don't open a public issue for a security hole. Follow
[`SECURITY.md`](https://github.com/ChristopherMarais/barkandambrosiagallery/blob/main/SECURITY.md).
