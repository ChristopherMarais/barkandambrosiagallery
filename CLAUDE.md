# Notes for Claude

## Deployments and the server: the owner only
- Never deploy this site. Do not run or dispatch the "Deploy App" workflow (`.github/workflows/deploy.yml`), publish a
  release (that deploys too), or run anything against the production server.
- Do not start any workflow that touches the server (Dropbox Backup, access reminders, game scores, ...) unless the
  owner asks for that exact workflow in so many words.
- Never merge pull requests. Open them; the owner reviews, merges and deploys.

## Secrets
- `.env.prod` lives only on the server (`/opt/barkandambrosiagallery/.env.prod`) and is gitignored: never commit it.
- Never print or paste passwords or keys. For checks, compare fingerprints (e.g. `md5sum`), never the values.
- Never run `git clean -fdx` on the server.

## Working style
- Small, reviewable PRs, one topic each, with tests. New tests go in new files where possible, so open PRs merge cleanly.
- Grey/white/black theme; colour only where it carries meaning. Keep player-facing text short.
- The house style is written down at the top of `beetlesgallery/static/css/input.css`: three button looks
  (`.btn-main` light grey with dark bold text, `.btn-primary` dark grey with white text, `.btn-secondary` white with
  a grey outline; never black), dark-grey ticks, numbers grouped in threes. Follow it on new pages.
