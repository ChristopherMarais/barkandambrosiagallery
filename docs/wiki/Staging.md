# Staging

The staging site, **https://staging.barkandambrosiagallery.org**, runs the latest `main` on the production server,
next to the live site. It has its own database, filled from a scrubbed copy of production's data. Use it to try
merged work on real data before a release.

- **Sign in:** with the shared account **stagedtesting** / **gallerystaging**. Every page needs it, and no other
  account can sign in. Every page has a red **STAGING** bar.
- **Safe to try anything:** uploads, edits, deletions, game rounds and approvals stay on staging. Images are
  production's, seen through a copy-on-write layer: what staging changes never reaches production's files. No email
  is ever sent; it goes to the log.
- **Code:** updates on every push to `main`, through the *Deploy Staging* workflow.
- **Data:** refreshed only on demand, by the owner (*Actions → Deploy Staging → Run workflow → Refresh data*). A
  refresh replaces everything done on staging since the last one.
- **Copied data is scrubbed:** every email address is replaced, and every real account is locked.

The setup, run once by the owner, and the details are in
[`docs/staging.md`](https://github.com/ChristopherMarais/barkandambrosiagallery/blob/main/docs/staging.md).

## Typical use

1. Your PR is merged. A few minutes later staging runs it. The version is shown in the footer as
   `staging-<commit>`.
2. Sign in as stagedtesting and check the change, on a phone as well.
3. Found a problem? Open an issue or a fix PR. Production is untouched until the next release.
