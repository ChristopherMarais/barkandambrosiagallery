# Lighthouse: speed, accessibility and best practice checks

Lighthouse (Google's page checker) scores each page for speed, accessibility, best practice and SEO.
The settings are in `lighthouserc.json`.

## Run it on your computer (Docker running, the site up)
1. Start the site: `docker compose up -d`
2. In PowerShell, from the repo folder: `npx -y @lhci/cli@0.15.1 autorun --config=lighthouserc.json`
3. Reports land in `.lighthouseci/` (open the HTML files in your browser).

Each page runs 3 times and the scores are the median. Warnings show when a score falls below the set level.

## Notes
- Only public pages are checked. Pages behind sign-in (the gallery, the game) need a sign-in step first, which we can add later.
- Staging asks for its own sign-in on every page, so this check doesn't run there yet.
- Nothing here runs on a schedule or on the live site.
