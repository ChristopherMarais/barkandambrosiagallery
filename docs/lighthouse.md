# Lighthouse: speed, accessibility and best practice checks

Lighthouse (Google's page checker) scores each page for speed, accessibility, best practice and SEO.
The settings are in `lighthouserc.json`.

## Run it on your computer (Docker running, the site up)
1. Start the site: `docker compose up -d`
2. In PowerShell, from the repo folder: `npx -y @lhci/cli@0.15.1 autorun --config=lighthouserc.json`
3. Reports land in `.lighthouseci/` (open the HTML files in your browser).

Needs Node.js (check with `node -v`; get the LTS version from nodejs.org if it is missing). Nothing is installed into the project.

Each page runs 3 times and the scores are the median. Warnings show when a score falls below the set level.

## Pages checked
Only pages anyone can open, so no sign-in is needed. The list is in `lighthouserc.json` under `collect.url`:

- `/` (the landing page), `/beetles/` (the gallery), `/interactions/`, `/tools/classify/`
- `/accounts/login/`, `/accounts/signup/`

These match the public pages in the smoke tests (`beetles_app/test_pages.py`). If a page becomes public or private, move it.

## Notes
- Pages behind sign-in (My Account, the game, the taxonomy browser) are not checked. Lighthouse would need a sign-in
  step with a test account, and its password would have to be kept out of git. Not done yet.
- Staging asks for its own sign-in on every page, so this check doesn't run there.
- Nothing here runs on a schedule or on the live site.
- The page scores are checked against the local copy of the site, which has no analytics (`GA_MEASUREMENT_ID` is empty).
