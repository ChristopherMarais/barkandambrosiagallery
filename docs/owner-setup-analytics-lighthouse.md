# Owner checklist: Google Analytics and Lighthouse

Only you can do these steps. Do them in order. Steps marked **(optional)** can be skipped.
Never put the Measurement ID in git. It goes only in the server's `.env.prod`.

## A. Google Analytics (about 20 minutes)

1. Open https://analytics.google.com and sign in with the Google account you want to own the data.
2. Click **Start measuring** (or **Admin**, bottom left, then **Create account**).
3. Account name: `Bark and Ambrosia Gallery`. Leave the data-sharing boxes as they are. Click **Next**.
4. Property name: `Bark and Ambrosia Gallery`. Pick your time zone and currency. Click **Next**.
5. Business details: choose the closest option or skip. Click **Create**, and accept the terms.
6. Choose **Web**. Website URL: `https://barkandambrosiagallery.org`. Stream name: `Live site`. Click **Create stream**.
7. Copy the **Measurement ID**. It starts with `G-` and has about ten letters and digits. Keep it in a safe place (not in an email to anyone else).
8. Still in the stream, click the **gear** icon next to **Enhanced measurement**. Switch **off** **Site search**
   (it can record the words people search for). Leave the other switches as they are. Click the back arrow.
9. In **Admin** (bottom left), under the property column, click **Data collection** (or **Data settings > Data collection**).
   Switch **Google signals** **off**. The site does not send Google signals data, and this keeps it that way.

## B. Put the ID on the live server (about 10 minutes)

10. Open a terminal and connect: `ssh you@your-server`. Then run `cd /opt/barkandambrosiagallery`.
11. Add one line to `.env.prod` (replace the example with your own ID, no quotes, no spaces):
    `echo "GA_MEASUREMENT_ID=G-XXXXXXXXXX" >> .env.prod`
    (Or open the file with `nano .env.prod` and add the line at the bottom. Save with Ctrl+O, Enter, Ctrl+X.)
12. Check the line by fingerprint only, never by reading the value out. In Git Bash on your computer run
    `echo -n "GA_MEASUREMENT_ID=G-XXXXXXXXXX" | md5sum`, and on the server run
    `grep "^GA_MEASUREMENT_ID=" .env.prod | md5sum`. The two codes must match. If they do not, edit the line again.
13. Restart the web container so it reads the file:
    `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d web`
14. Check the site: open https://barkandambrosiagallery.org in a private window, right-click, **View page source**,
    and search for `consent-banner`. It should be found.

## C. Approve the cookie wording

15. Read the notice (it appears at the bottom of the site in a private window):
    > Can we use Google Analytics to count page visits? We never send it your name or your account.
    > Your choice is remembered for a year.
    Buttons: **Accept** and **Reject** (same size and style, on purpose). If you want different words, tell Claude the new text.
    The exact text is in `docs/analytics.md` under "Consent".
15a. Check the privacy notice at `/privacy/` and fill in the two facts only you know: the legal name of the controller
    (it says "The Bark and Ambrosia Gallery") and, in `.env.prod`, add `PRIVACY_CONTACT_EMAIL=you@example.org` (same
    way as step 11). Restart the web container (step 13) so the contact line appears.
15b. In GA4, **Admin > Data collection > Data retention**, set **Event data retention** to **14 months**. The privacy
    notice says 14 months. Then click **Save**.

## D. Check that the numbers arrive

16. In that private window, click **Accept**. Then in GA4 open **Reports > Realtime**. You should see yourself as one user
    within a minute or two. Open a game page and finish one round; **Realtime** shows the events.

## E. Google Search Console (optional, for search visibility)

17. Open https://search.google.com/search-console and sign in. Click **Add property**, choose **URL prefix**, and enter
    `https://barkandambrosiagallery.org`.
18. Google asks you to prove you own the site. Follow the offered method (usually adding a TXT record at your domain
    registrar, or pasting an HTML tag). Click **Verify**.

## F. Lighthouse speed and accessibility check (optional, on your computer)

19. In PowerShell run `node -v`. If it prints a version number, there is nothing to install. If not, install the LTS version of Node.js from nodejs.org (the only install this needs).
20. Start Docker Desktop, and in a PowerShell window open the repo folder (`D:\GIT_REPOS\barkandambrosiagallery`).
21. Run: `docker compose up -d`
22. Then run: `npx -y @lhci/cli@0.15.1 autorun --config=lighthouserc.json`
    It takes a few minutes. Warnings are normal; they are not errors.
23. Open the reports in the `.lighthouseci` folder (HTML files, open in your browser). Scores are listed at the top of each.

## Where to see the results

- Visitors and pages: GA4 **Reports > Realtime** (now) and **Reports > Engagement > Pages and screens** (over time).
- Game and sign-up events: GA4 **Reports > Engagement > Events** (`game_start`, `game_round_done`, `sign_up_started`,
  `sign_up_done`).
- Lighthouse scores: the `.lighthouseci` folder in the repo on your computer (it is not in git).

## Notes

- Visitors who click **Reject** are not counted; the choice is kept on their computer for a year.
- To stop analytics, delete the `GA_MEASUREMENT_ID` line from `.env.prod` and restart the web container (step 13).
