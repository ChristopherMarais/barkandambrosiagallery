# Analytics (Google Analytics 4)

Google Analytics shows how many people visit each page and use the game and sign-up. It runs **only on the live
site**, and only after a visitor clicks **Accept** on the cookie notice.

Owner steps (create the account, put the ID on the server): `docs/owner-setup-analytics-lighthouse.md`.

## How it is switched on

- The setting is `GA_MEASUREMENT_ID` (`beetlesgallery/settings.py`), read from the environment. The live server sets it in
  `.env.prod`. It looks like `G-XXXXXXXXXX`.
- Empty (the default: local, staging, tests) means **nothing** is rendered: no script, no notice, no cookie. A value that is
  not a GA4 ID (`G-` followed by letters and digits) is ignored the same way.
- Code: `beetlesgallery/beetles_app/analytics.py` (the context processor), `templates/base.html` (the loader tag and the
  notice), `static/js/analytics.js` (consent, the page view and the named events).

## Consent (EU: ePrivacy/PECR and GDPR)

Nothing non-essential runs before a clear choice.

- **Default is denied.** Before anything else, the script sets `analytics_storage`, `ad_storage`, `ad_user_data` and
  `ad_personalization` to denied (Consent Mode v2). The Google script (gtag.js) is not downloaded until **Accept**.
- **The notice** (`templates/base.html`, `#consent-banner`) shows on public pages until the visitor chooses. It is a solid
  white panel with a grey border, pinned to the bottom above the content. **Accept** and **Reject** are the same size and
  style (both `btn-secondary`, one grid cell each), and no box is pre-ticked. On the game page the game screen stops above
  the panel (`--consent-h`), so its buttons stay usable on a phone.
- **Withdrawing** is as easy as giving: **Cookie settings** in the footer (every page) reopens the notice. **Reject**
  stops analytics straight away (consent updated to denied) and deletes the `_ga` cookies from the browser.
- **The choice** is stored in `ga_consent` (`granted` or `denied`) for 365 days, and only once the visitor has chosen.
  It is an essential cookie: it only remembers the choice; nothing tracks with it.
- **Do Not Track is not used.** The notice asks, so the choice stays the visitor's. Honouring a browser flag would skip
  the question silently, and would not replace consent under the EU rules.
- **Privacy notice** at `/privacy/` (`templates/privacy.html`), linked from the notice and the footer. It names the
  controller, what is collected, Google as processor, retention, rights and how to withdraw. The contact address comes
  from `PRIVACY_CONTACT_EMAIL` (set in `.env.prod`); the line is left out when it is empty.
- Current wording (for the owner to approve):
  > **May we count your visits?**
  > We would like to set an analytics cookie from Google Analytics, which Google runs for us. It counts page visits and
  > which games are played, so we can see what to improve. It is set only if you accept.
  > Your choice is kept in an essential cookie for a year. You can change it at any time with **Cookie settings** at the
  > bottom of a page. Privacy notice
  Buttons: **Accept**, **Reject**.

## What is sent to Google

- **Page views**: the page's URL *pattern* from `urls.py`, not the address. `/game/rounds/3f2c.../` is sent as
  `/game/rounds/:id/`. No query string, no beetle, player or other ID, no username. The page title is sent as that same
  pattern. Referrers are reduced to their site (origin) only.
- **Four named events**, each with only the fields listed (anything else is dropped in `analytics.js`):

| Event | When | Fields |
|---|---|---|
| `game_start` | a game page is opened (`game_play.html`, once per page load) | `game`: the game's short key, e.g. `odd`, `select`, `mixed` |
| `game_round_done` | a round of beetles is finished (its summary is shown) | `answers`: how many answers the round had |
| `sign_up_started` | the first key press in the sign-up form (`request_access.html`) | none |
| `sign_up_done` | the "check your email" / "request sent" page (`request_access_sent.html`) | none |

- Nothing else is sent by our code. Google Signals and ad personalisation are switched off in the tag
  (`allow_google_signals: false`), and the owner turns off Google Signals in the GA4 settings too (see the owner checklist).
- Google itself still sees each visitor's IP address and browser details, as with any GA4 site. That is what the notice says.

### Things to know

- `game_start` counts page loads of a game page, so a reload counts again.
- `sign_up_done` counts a reload of the "sent" page again.
- GA4's built-in "Enhanced measurement" can record **site search** words (from `?q=`). Turn that off in the GA4 settings
  (owner checklist) so search words never reach Google.

## Adding or changing an event

1. Add the name and its allowed fields to `EVENTS` in `static/js/analytics.js`. Only plain values: a short key or a count.
   Never a name, an account, an ID or an address.
2. Call it from the page: `if (window.gaTrack) gaTrack("name", { field: value });`, or mark a page with
   `data-ga-event="name"` (fires on load) or `data-ga-start="name"` (fires on the first key press in that form).
3. Add the name to the list in `beetles_app/test_analytics.py` (`test_the_script_sends_only_the_named_events`).

## Turning it off

Remove `GA_MEASUREMENT_ID` from `.env.prod` and restart the web container. The notice and the script disappear with it.

## Checking it works

- Local: leave the ID empty, the site renders with no analytics at all (the default).
- Live, after the owner sets the ID: open the site in a private window, view the page source and search for
  `consent-banner` (present), then click **Accept** and watch GA4 **Reports > Realtime**.
- Optional, for the team: GA4 **Admin > DebugView** shows the events of a browser that has the
  "Google Analytics Debugger" extension or `?debug_mode=1` set up, so the `page_location` values can be checked.

## Tests

`beetles_app/test_analytics.py`:

- with the ID empty (and an invalid one): no tag, no notice, none of the analytics markers in the page;
- with the ID set: the loader carries it, the notice is on the public pages (`/`, login, sign-up);
- nothing personal (username, email) in the loader or the notice, even when signed in;
- the page pattern has `:id` in place of IDs, `/not-found` for unknown paths, and no query string;
- the script names only the four events above, with no personal fields.

Run: `pixi run python manage.py test beetlesgallery.beetles_app.test_analytics --noinput`
