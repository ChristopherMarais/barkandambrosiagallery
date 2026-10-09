# Bark & Ambrosia Detective (the Beetle ID game): Specification

Status: built on branch `claude/funny-tesla-pi0iki` (PR #255). Classifier integration is out of scope (see section 12).

## 1. Purpose

1. Collect species labels for images that don't have verified labels yet.
2. Measure how reliable each player's labels are, using validated images the player doesn't know are being scored.
3. Let proven experts' labels reach curators as trusted proposals.
4. Help players learn through feedback after each round, and let them flag labels they think are wrong.

## 2. Access and placement

| Requirement | Detail |
|---|---|
| Entry point | the game's card on the home page, plus one entry in the mobile and desktop sidebars (with the player's level and points) |
| Players | Logged-in users only. Accounts are created by staff |
| Staff | Review page, player reports, proposals and report handling on the annotation page |
| Devices | Mobile-first: native pickers, large tap targets, action bar within thumb reach, tap a photo to see the whole image |
| Style | Matches the site: gray palette, `font-semibold` headings, `rounded-lg` gray-200 panels, Flaticon icons |

## 3. What's in the game

- **Playable ROI:** a `Beetles` row with a bounding box (width and height > 0), not soft-deleted, on a live image that has a file.
- **Scored items ("checks"):** playable ROIs with `bbox_is_validated = True`, a taxon with at least subfamily and genus, and no open player report.
- **Unscored items:** playable ROIs with `bbox_is_validated = False`. These are where labels are collected.
- **Taxonomy:** four ranks: subfamily, tribe, genus, species. Subtribe isn't used.
  - Malformed rows in the species list (shifted columns, e.g. an epithet in the subfamily column) are excluded.
  - Subspecies rows are merged into their genus + species.

## 4. Game modes

### 4.1 Name the beetle (classify)
- Shows a crop of one ROI (the box plus 8% margin). Tap to see the whole image with the box outlined.
- The player answers with cascading pickers (subfamily → tribe → genus → species), where each list narrows the next.
  - Picking a genus fills in its subfamily and tribe.
  - A search box jumps straight to a genus or species.
- Players can stop at any rank. Unanswered ranks stay "Not sure" and aren't scored.
- Answers must match the taxonomy, otherwise they're rejected.

### 4.2 Spot the relatives (pair)
- Shows two ROI crops side by side, labelled A and B.
- One choice: different subfamily / same subfamily / same tribe / same genus / same species / not sure.
- **Scored pairs:** two validated ROIs.
- **Unscored pairs:** one unvalidated ROI next to one validated ROI, so the answer implies a label for the unvalidated one.
- The partner is picked at a random relationship (same species, genus, tribe, subfamily, or different), so answers are spread across ranks.
- The two ROIs always come from different images, and their left/right order is random.

### 4.3 Odd One Out (odd, #369; called Imposter Picker for a while)
- Shows 4, 9 or 16 ROI crops in a 2×2, 3×3 or 4×4 grid. All but one share a name at one rank; the player selects the one that doesn't ("Find the one that doesn't share the same genus"), then Next.
- **The grid ladder** (`game_grid_ladder`, #489), one per player and grid game: 12 steps, the size first and then the rank: 4, 9, 16 at subfamily, then the same at tribe, genus and species. Up a step after `GAME_GRID_UP_AFTER` (2) good grids in a row, down one after a poor grid (never below 1, never past the player's open ranks), starting at `GAME_GRID_START_STEP` (1). Good: the odd one found; poor: a validated beetle of the rest picked; a pick on an unvalidated ROI, a skip or a grid ended by flags is neither. A step that moves mid-batch rebuilds the batch's later grids.
- When the ROIs for the step are short the grid falls back instead of giving up: the same grid without the sure + unsure AI pair, then smaller at that rank, then the nearest other ranks (shallower first). With a focus, only ranks below it. On harder rounds the odd one is a near relative (same parent, e.g. another genus of the same tribe).
- The odd one and at least one of the rest are always validated. Some of the rest are unvalidated ROIs IBBI-AI puts in the group, more as the player levels up (`GAME_ODD_OPEN_SHARE_*`): when its predictions allow, one it is sure of (≥ `GAME_AI_SURE_FROM`, 0.9) and one it is unsure of (< `GAME_AI_UNSURE_BELOW`, 0.6). No two crops come from one image.
- **Scoring** (`game_scoring.odd_truth`, `odd_consensus`): a pick on a validated ROI is judged at once, right when it isn't in the group. A right pick earns `GAME_POINTS_ODD_WEIGHT` (1.5) × the Family Ties points for how related the odd one is to the rest × the size factor (`GAME_GRID_SIZE_FACTOR`: 4 → 1, 9 → 1.5, 16 → 2); a wrong pick costs `GAME_POINTS_ODD_WRONG_FACTOR` (1.25) × that; **Skip earns** `GAME_POINTS_ODD_SKIP` (0.25). A pick on an unvalidated ROI is scored later by agreement that it isn't in the group (judges' names, experts, a trusted model), like a name on it. When the odd one is found, the unvalidated ROIs left with the rest count as weak votes for the group (`GAME_SELECT_TAP_WEIGHT`).
- **Flags:** a photo flagged from the grid goes to the curators and drops out of the grid, which carries on; it counts for nothing. Half the photos flagged, or the odd one, ends the grid unscored (skipped and held).
- After answering, the odd one is outlined and named at the round's rank (it is always validated, so this is the truth); the odd one is then never scored for that player again.
- Unlocks at level 2; Naming moves to level 4. Players who had Naming keep it (`GamePreference.kept_perks`).

### 4.4 Find Them All (select, #370; first called Select all)
- Shows 4, 9 or 16 ROI crops and a group: "Select every Platypodinae · 9 beetles". The size and the rank follow the player's step on the grid ladder, with the same fallbacks, as in Odd One Out. Good: no wrong tap and at least `GAME_GRID_GOOD_SHARE` (75%) of the validated members found; poor: more wrong taps than right ones, or none right.
- Validated members are about a quarter to under half of the grid (4: 1–2, 9: 3–4, 16: 5–7); the rest are validated beetles of other groups (near relatives on harder rounds), never fewer than the members, so tapping everything always loses. Unvalidated ROIs IBBI-AI puts in the group make up 1 of 4, up to 3 of 9 and up to 4 of 16, more as players rise (a sure and an unsure one when its predictions allow): taps on those are recorded (`picks`) and never scored. No two crops come from one image.
- **Scoring** (`game_scoring.select_truth`): each validated member tapped earns a share of `GAME_POINTS_SELECT_WEIGHT` (2) × the Family Ties points for the grid's rank × the size factor, so a perfect grid of 4 earns about twice a Similarity answer; each validated non-member tapped costs `GAME_POINTS_SELECT_WRONG` (1.5) shares; a member left out, or flagged, costs nothing. Skip earns `GAME_POINTS_ODD_SKIP`. `correct_<rank>` records whether the grid was perfect; a grid counts once in the reliability rating (#381).
- After answering: green for members tapped, red for wrong taps, a dashed line round members left out.

### 4.5 Every mode
- Every item has a **Skip** button.
- There's no feedback during a round: nothing says right or wrong, and the score doesn't change. (Odd One Out is the exception: it shows the odd one after each answer, see 4.3.)
- The browser receives only an image URL and a box, never the ROI ID, its label, or whether the item is scored.

## 5. Rounds

| Rule | Value |
|---|---|
| Items per round | 10 (`GAME_ROUND_SIZE`) |
| Share of scored items | 60% until the player has 20 scored answers in that mode, then 20% (`GAME_CHECK_RATIO_NEW` / `_KNOWN`, `GAME_CALIBRATION_CHECKS`). At least one per round |
| Pool shortfall | If one pool runs out, the round is topped up from the other |
| Item order | Shuffled |
| Resume | Reloading picks up an unfinished round from the last 12 hours (`GAME_RESUME_HOURS`) |
| Loading speed | The next item's photos are downloaded while the current one is answered |
| Safety | Answers must arrive in order. A duplicate or out-of-order submit gets a 409 and the round resumes |
| Timing | The response time for each answer is recorded |
| Unscored repeats | Items the player already answered are avoided, and only reused if the pool is too small |
| Scored repeats | **Never.** A validated ROI the player has been shown (as a scored item or a pair partner) is never scored for them again |

## 6. Choosing images: difficulty and focus

- **Difficulty (0 = easy, 1 = hard)** is stored per ROI in `RoiDifficulty`:
  - `model_difficulty`: an empty slot for classifier output, which takes priority once filled.
  - `game_difficulty`: learned from answers. For scored items it's the error rate (smoothed); for unscored items it's how much players disagree on the genus. It's recomputed at the end of each round.
  - Unknown difficulty counts as 0.5.
- **Each player's target difficulty** is `min(0.9, 0.2 + 0.02 × rounds finished + 0.3 × skill)`, where skill runs from 0 (coin-flip accuracy) to 1 (perfect). So the game always gets harder over time, and faster for accurate players.
- **Selection:** about 6 random candidates are drawn per slot (by jumping to a random ID rather than sorting the table randomly), then weighted toward ones near the target difficulty.
- **Focus:** half of the scored items in a classify round come from genera or tribes the player has recently been labelling but hasn't proven themselves in yet, so they get the chance to earn trust there.

## 7. Scoring

- **Classify:** each answered rank is marked right or wrong against the validated taxon. Species counts as right only if genus and epithet both match. Ranks the player left blank, or that the reference lacks, aren't judged.
- **Pair:** the answer "same X" claims the pair shares every rank down to X and differs below it. Each of those claims is judged. "Not sure" isn't judged.
- **Accuracy** is correct judged ranks ÷ judged ranks, counting scored answers only and excluding held answers (section 9). It's shown once the player has at least 10 judged ranks.
- **Reliability weight** for combining votes is `(correct + 1) / (judged + 2)` per rank.

## 8. Expertise and trusted labels

- **Skills are tracked per rank within a branch:**
  - species within a genus
  - genus within a tribe
  - tribe within a subfamily
  - subfamily overall
- Only scored "Name the beetle" answers count. Each validated ROI counts once, using the player's first answer.
- **Proven** means at least 15 distinct scored examples in that branch, with the 95% lower bound on accuracy (Wilson score) at least 0.90. With the defaults, a perfect record needs **35** answers. Settings: `GAME_TRUST_MIN_JUDGED`, `_MIN_LOWER_BOUND`, `_Z`.
- **Untestable branches:** a branch with fewer validated ROIs than the number needed to prove competence accepts proof in 2 or more sibling branches instead (`GAME_TRUST_SIBLINGS`).
  - Species in an untested genus → species-level proof in 2 other genera of the same tribe.
  - Genus in an untested tribe → proof in 2 other tribes of the same subfamily.
  - Tribe in an untested subfamily → proof in other subfamilies.
- **Expert-backed label:** at a given rank, at least 1 proven player supports the winning value (`GAME_TRUST_MIN_VOTES`), no proven player disagrees, and every rank above it is expert-backed too.
- **Proposals are for staff to review:** nothing writes to ROI records automatically.
- Skills are recomputed at the end of each round and whenever a report is resolved.

## 9. Feedback and reports

### 9.1 After a round
- A "Your answers" page for every finished round, reached from the round summary and from "My performance".
- **Verified items:** the player's answer next to the validated label, ✓/✗ per rank, and an overall verdict (Right / Partly right / Not quite).
- **Unverified items:** "Not verified yet", the current (unverified) label, and what other players have said.
- **Pairs:** the true relationship when both are verified, and each image's label.
- A summary line: "X of Y verified beetles fully right".
- **Changed requirement:** players are unaware which items are scored *during* a round. After a round, feedback reveals it. Section 5's never-repeat rule protects scoring integrity.

### 9.2 Reporting an ROI
- From the review page, a player can report any image: the name looks wrong, the box doesn't fit, a photo problem, or something else, plus an optional note. One open report per player per ROI.
- While a report is open:
  - The ROI is excluded from scoring for everyone.
  - The reporter's scored answers on it are held (`GameAnswer.score_hold`), so reporting never costs them.
- **Resolved by staff:**
  - **"Label was wrong · fixed":** every scored answer on the ROI (in either mode) is re-scored against the corrected label. If the ROI is no longer validated, those answers are voided for everyone.
  - **"Label is correct":** the held answers count again.
- A report does **not** change `bbox_is_validated` by itself.

## 10. Label proposals for curators

- **Consensus per unvalidated ROI:** votes from both modes, weighted by each player's reliability at that rank.
  - A pair answer "same genus" votes for the validated partner's subfamily, tribe and genus.
  - "Different subfamily" and "not sure" imply nothing.
- **Annotation page (on each ROI):**
  - The game proposal (value, weighted support and votes per rank, with a shield on expert-backed ranks).
  - **Use \<species\>:** sets `depicts_valid_name_id`. It doesn't validate the ROI, and it's refused if another user has the image locked.
  - **Dismiss.**
  - Both decisions are logged in `LabelReview`.
- Open player reports are shown on the ROI with the two resolve buttons. A link of the form `?image=<id>` opens a specific image.

## 11. Pages and reports

| Page | Who | Content |
|---|---|---|
| `/game/` | Players | Items labelled, accuracy, the two modes, leaderboard (sort by labelled or accuracy) |
| `/game/play/<classify\|pair>/` | Players | The game |
| `/game/rounds/<id>/` | The round's owner, or staff | Feedback on that round and reporting |
| `/game/me/` | Players | Headline stats, challenge level, expert areas, progress toward the next ones, accuracy by rank and by month, recent rounds |
| `/game/players/<id>/` | Staff | Any player's report |
| `/game/settings/` | Superuser | Game settings. Review game labels: open reports queue, player table with expert badges, proposals (all or expert-backed), CSV exports: proposals, reliability, expertise. Unlocks: grant players unlocks. (`/game/review/` and `/game/staff/unlocks/` redirect here.) |
| Django admin | Staff | GameRound, GameAnswer, PlayerSkill, LabelReview, RoiDifficulty, GameReport |

Privacy rules for the player report:

- Progress is listed only for groups the player has named themselves, and only after at least 5 scored answers there (`GAME_REPORT_MIN_JUDGED`), so the report never gives away the true label of a scored item.
- Scores and reports only update at the end of a round.

## 12. Data model (migrations 0018–0020)

| Table | Purpose |
|---|---|
| `game_round` | A player's round: mode, the item list chosen when it started, start and finish times |
| `game_answer` | One answer: the ROI(s), the per-rank answer or pair answer, per-rank correctness, a snapshot of the reference label, response time, `score_hold` |
| `game_player_skill` | Correct / judged / lower bound / proven / proven-at, per player, rank and branch |
| `game_label_review` | Staff accept or dismiss decisions on proposals |
| `game_roi_difficulty` | Difficulty per ROI: classifier slot and game-derived value |
| `game_report` | Player reports and how they were resolved |

## 13. Out of scope / future

- Superusers uploading classifier predictions, and running the classifier on new images or bulk uploads. These will fill `RoiDifficulty.model_difficulty` (and later could feed model proposals).
- Optional: unvalidate an ROI automatically after N reports from different players.
- Optional: a notice to reporters when their report is resolved.

## 14. Settings summary

| Setting | Default |
|---|---|
| `GAME_ROUND_SIZE` | 10 |
| `GAME_CALIBRATION_CHECKS` | 20 |
| `GAME_CHECK_RATIO_NEW` / `_KNOWN` | 0.6 / 0.2 |
| `GAME_MIN_JUDGED_FOR_ACCURACY` | 10 |
| `GAME_REPORT_MIN_JUDGED` | 5 |
| `GAME_RESUME_HOURS` | 12 |
| `GAME_DIFFICULTY_START` / `_PER_ROUND` / `_SKILL_WEIGHT` / `_MAX` | 0.2 / 0.02 / 0.3 / 0.9 |
| `GAME_CANDIDATE_OVERSAMPLE` | 6 |
| `GAME_TRUST_MIN_JUDGED` | 15 |
| `GAME_TRUST_MIN_LOWER_BOUND` | 0.9 |
| `GAME_TRUST_Z` | 1.96 |
| `GAME_TRUST_SIBLINGS` | 2 |
| `GAME_TRUST_MIN_VOTES` | 1 |

## Update: one continuous feed (supersedes sections 4.3, 5 and the round wording elsewhere)

- **No rounds for the player.** They get a continuous feed of beetles until they tap **Exit**. Behind the scenes the feed is
  still stored as batches (`GameRound`, 10 items), so scoring, skills, difficulty and the "Your answers" pages are unchanged;
  when a batch ends the next one starts in the same response and the player never sees a break. The feed only ends
  ("You're all caught up") when there is nothing new left to show (`start_round(..., fresh_only=True)`).
- **Checks are dropped in now and then.** Within a batch the scored items are spread evenly with a random start (`game.spread`),
  instead of a plain shuffle, so they are neither clumped nor a predictable rhythm. The 60% / 20% ratio is unchanged.
- **Names:** *Name the beetle* is **Name That Beetle**; *Spot the relatives* is **Family Ties**. (Display only: the stored mode
  values and model labels are unchanged, so there is no migration.)
- **No live score.** There is no progress bar, count or accuracy while playing. Scores are on the game home and in
  My performance. Leaving with **Exit** closes the current batch (`game_exit`) so the answers count at once; a batch that was
  left open (tab closed) is closed the next time they open the game home (`close_idle_rounds`, after 10 idle minutes).
- **Confetti** for a scored item answered right: the species in Name That Beetle, or every judged claim in Family Ties
  (never for "Not sure"). It is the only hint that an item was scored, and only when the player won.
- **Layout (phones first).** One fixed screen: header (Exit, title, search/help), photos, answer panel, and a fixed action row.
  Nothing moves between beetles, so the buttons are always in the same place.
  - Name That Beetle: four stacked lists, broad to specific (subfamily, tribe, genus, species), then **Skip** / **Next**.
    Search by name is a button in the header.
  - Family Ties: a vertical ladder from *different subfamily* (top) to *same species* (bottom) that fills like a meter and can be
    tapped or dragged, then **Not sure** / **Next**. On a phone the two photos sit one above the other.
  - Colours: grey, black and white only. Colour appears only for results and errors, and on the confetti.

## Update: keeping people playing (rewards)

All derived from the player's answers (`game_rewards.py`), nothing new is stored, no migration.

- **Daily goal** (`GAME_DAILY_GOAL`, default 20 beetles) and a **day streak** (consecutive days with an answer; still alive
  until the day after the last one ends). The feed's header shows a small `today/goal` chip; its flame turns amber only when
  the goal is met, because that is what the colour means.
- **Levels** from the number of beetles labelled: Egg, Larva, Pupa, Teneral, Beetle scout, Field entomologist,
  Taxonomist, Beetle master, Coleopterist.
- **Badges**: first steps, 10 / 100 / 1,000 beetles, 3 / 7 / 30-day streaks, daily goal, both games, first species right,
  25 species right, trusted expert.
- **Toasts while playing** for a level up, the daily goal, the first answer of a streak day and count milestones (10, 25, 50,
  100, 250, 500, 1,000). They never mention accuracy (a test checks the words), so there is still no live score.
- **Recap when you tap Exit**: beetles labelled this sitting, how many of the known beetles were right (the one place the
  score appears, after leaving), the streak, any new badge. Then "Keep playing" or the game home.
- **Game home**: level and progress bar, daily goal, streak, all badges (earned and locked), and a leaderboard that can be
  *this week* (resets Monday) or all time, by beetles labelled or accuracy.

## Update: points (game_scoring.py)

Every answer is worth points (`AnswerPoints`); a player's score is the running total, never below zero (`PlayerScore`).
Full rules for players are on the "How scoring works" page (`/game/how-it-works/`).

- **Validated beetles (truth)** earn the most. Name That Beetle: subfamily 1, tribe 2, genus 4, species 8 when right,
  minus 75% of that when wrong (`GAME_POINTS_WRONG_FACTOR`). Family Ties: by the true relation, different subfamilies 1,
  subfamily 2, tribe 3, genus 5, species 5, plus up to 25% for alike photos (photographer, institution, magnification,
  country, aspect; `GAME_POINTS_SIMILARITY_BONUS`); wrong loses 1 per step off (`GAME_POINTS_PAIR_STEP`).
- **Unvalidated beetles (agreement)**: up to 60% (`GAME_POINTS_CONSENSUS_CAP`) of the truth points, never negative.
  Judges are players with at least `GAME_RATER_MIN_JUDGED` (10) judged ranks whose *rating* is at or above the median.
  A proven expert for that branch weighs 1; anyone else weighs `1 / (1 + exp(-(their rating - your rating) / 0.1))`.
  Per rank, agreement `c = (agree - disagree) / (agree + disagree + 1)`, points `cap x rank points x max(0, c)`.
- **Rating**: the lower end of a Wilson interval (z = 1) of the player's judged ranks on validated beetles,
  first sightings only. It is what decides whose agreement counts; the score is what decides unlocks.
- **Not sure / skip**: -0.25 (`GAME_POINTS_UNSURE`).
- **Retries**: a validated beetle answered wrong comes back after `GAME_RETRY_AFTER_DAYS` (2), at most
  `GAME_RETRY_MAX` (3) times, `GAME_RETRY_PER_BATCH` (1) per batch, marked "Seen before". It earns half
  (`GAME_POINTS_RETRY_FACTOR`) and is left out of accuracy and expertise (`GameAnswer.is_retry`).
- **Retroactive**: points are recomputed for a player when they leave the game and for everyone nightly
  (`manage.py recompute_game_scores`, "Nightly game scores" workflow). Answers on beetles validated since are then
  scored against the truth.

## Update: levels, perks, focus, expertise tree (game_levels.py)

- **Levels need points and reliability** (reliability = the rating). Egg (0), Larva (50: focus a subfamily),
  Pupa (150, 35%: focus a tribe), Teneral (400, 50%: focus a genus), Beetle scout (800, 60%: **labels go to
  curators as suggestions**), Field entomologist (1500, 70%), Taxonomist (3000, 75%), Beetle master (6000, 80%),
  Coleopterist (10000, 85%). Levels can drop if reliability drops; perks follow the current level.
- **Suggestions to curators** (annotation page proposals) only count answers from players at the suggestions level
  or proven experts somewhere (`GAME_PROPOSALS_NEED_LEVEL`, default on).
- **Experts' labels without review**: when at least `GAME_AUTO_APPLY_MIN_EXPERTS` (2) experts agree down to
  species with no expert disagreeing, on a beetle with no species label, not validated and never reviewed, the species
  is written to the beetle (still unvalidated) with a `LabelReview` that has no reviewer
  (`GAME_AUTO_APPLY_EXPERT_LABELS`, default on). Runs when a player leaves the game (their round's beetles) and nightly.
  Each expert counted must be **proven directly** in every branch of the label (subfamily; tribe within it; genus
  within the tribe; species within the genus): at least `GAME_TRUST_MIN_JUDGED` (15) answers on validated beetles
  there and a Wilson lower bound (95% confidence) of at least `GAME_TRUST_MIN_LOWER_BOUND` (90%), and be in the top
  `GAME_EXPERT_PERCENTILE` of players overall. Proof in neighbouring branches (the sibling rule for untestable
  branches) still backs a suggestion to curators, but never a label written without review.
- **Experts must also be in the top `GAME_EXPERT_PERCENTILE` (25%) by rating** once `GAME_EXPERT_MIN_PLAYERS` (10)
  players are rated.
- **Focus** (`GamePreference`): a player can limit the feed to one subfamily / tribe / genus when unlocked; falls back
  to everything when the focus has no beetles left.
- **Expertise tree** (`/game/expertise/`, anyone's at `/game/players/<id>/expertise/`): subfamily > tribe > genus,
  each coloured by accuracy (grey too few, red <60%, amber 60-85%, green 85%+, glowing green proven expert).
- **Unlocks page** (`/game/unlocks/`): the ladder, what is missing for the next level, what happens to your labels,
  and the focus picker.

## Update: the quick loop

- **After each answer** (#488) the main button changes from **Submit** to **Next** and a review takes the answer's
  place: on a validated beetle the true name, its tier and the points rank by rank (tile by tile in the grid games,
  names only to the grid's rank); on one nobody has validated yet what the other players (reliability-weighted, with
  the proven experts among them) and IBBI-AI say it is, with how sure they are. The confetti follows the points.
  Skip goes straight on; **Back** shows the last review again, also after a reload.
- **Before answering** a beetle others have named shows "Named by N other players" (the count only, so nobody is led).
- **Beetles others named come first**: about half (`GAME_PEER_SHARE`) of the unvalidated beetles in a batch are ones
  1 to `GAME_PEER_MAX_OTHERS` (4) other players have named and you have not, so names get second and third opinions.
- **Combo**: answers in a row without skipping show as x3, x4... in the header, with a toast at 10, 25, 50, 100.
- **Participation**: every real answer earns `GAME_POINTS_PARTICIPATION` (0.5) on top of its accuracy points, so the
  score grows with play. Skips do not.

## Update: leaderboard, profiles, annotation tips, help

**Leaderboard** (`/game/leaderboard/`, `game_board.py`). Ranks players by score, accuracy (shown after
`GAME_MIN_JUDGED_FOR_ACCURACY` judged answers) or beetles seen, all time or this week, with a name search. The
**specialists** board ranks players inside one subfamily (by their tribe answers), tribe (genus answers) or genus
(species answers), proven experts first, then by the cautious (Wilson) estimate. The game home shows the top 10.

**Profiles** (`/game/players/<id>/profile/`). Anyone signed in can open a player from the leaderboard: level, score,
accuracy, beetles seen, streak, games played, where they are a proven expert, badges, and a link to their tree.

**Tips on the Image Annotation page** (`game_tips.py`, served with `/game/api/proposals/` as `tips`). Only answers
from players whose labels reach curators count (`game_levels.suggestion_voters`).

* *Agreement*: the deepest rank backed by a proven expert, or by at least `GAME_TIP_MIN_VOTES` (3) players with at
  least `GAME_TIP_MIN_SUPPORT` (75%) of the weighted vote. Marked amber when it differs from the current label.
* *Not in*: a Family Ties answer against a validated beetle also says what the other beetle is not ("same tribe"
  means not the partner's genus; "different subfamily" means not its subfamily). Shown when at least
  `GAME_TIP_MIN_NOT_VOTES` (2) players say so and at least 75% of those who spoke to it agree.

**Help and feedback.** `/game/how-it-works/` opens with a 30-second guide, then scoring, levels, where labels go,
experts and the leaderboard. A link to the game's GitHub Discussions category (`GAME_DISCUSSIONS_URL`) is on the
game home, the help page and the end-of-game recap.

## Update: expertise that scales with the taxon, and new species found

**Expertise scales with the size of a taxon** (`game_trust.coverage`, `is_proven`). Proof is no longer a fixed
number of answers. For a skill (species within a genus, genus within a tribe, tribe within a subfamily, subfamily
overall), the player must have answered `GAME_TRUST_IMAGES_PER_SPECIES` (5) validated images of **every species in
that taxon that has validated images** (all of them for a species with fewer), at least `GAME_TRUST_MIN_JUDGED`
(10) answers in total, and be right at least `GAME_TRUST_MIN_ACCURACY` (90%) of the time. A genus with two species
needs about 10 answers, one with forty about 200. Taxa with fewer than `GAME_TRUST_MIN_JUDGED` validated images
can't be proven directly; there the sibling rule (proof in related taxa) still backs suggestions to curators, but
never labels written without review. `PlayerSkill` stores `required`, `covered`, `species_total` and
`species_done`, shown on the expertise tree and the performance page. For the ranks above a label's own, being
*reliable* (10+ answers, 90% right) is enough. The Wilson lower bound is kept only for ranking players.

Player-facing text says "taxon" rather than "group", which has its own biological meaning.

**New species found** (`game_discoveries.py`, `SpeciesDiscovery`). A Name That Beetle answer on an unvalidated
beetle is marked `new_species` when the gallery had no validated images of the named species at that moment. If a
curator later validates the beetle as exactly that species, the player gets a one-time pop-up on the game home and
a *New species finder* badge, with the species listed on their profile. No extra points. Checked when a player
leaves the game, when they open the game home, and nightly. Answers given before this release are not marked.

## Update: one game, one button

**One feed, both games** (`game.build_mixed_items`, `GameRound.Mode.MIXED`). The game home has a single Play
button. The feed mixes Name That Beetle and Family Ties at random, each item carrying its own `mode` (answers are
saved with it). Beginners get mostly Family Ties and experts mostly naming: the Family Ties share runs from
`GAME_PAIR_SHARE_START` (70%) at level 1 to `GAME_PAIR_SHARE_END` (15%) at level 10 (`game_levels.pair_share`).
About half of the open Family Ties beetles (`GAME_STUCK_SHARE`) are ones players tried to name but nobody took to
species (`game.stuck_rois`), so pairing them with known beetles narrows down what they are not.

**Unlocks in the feed.** A toolbar on the play screen holds the game toggle (Both / Name / Ties) and the focus
button. Locked choices show a lock and the level that opens them. Changes go through `POST /game/api/prefs/` and
start a fresh batch so they apply from the next beetle. `GamePreference.play_mode` stores the choice; it lapses if
the level drops.

**Levels, shifted up one, with a new top level:**

| Level | Name | Points | Reliability | Unlocks |
|---|---|---|---|---|
| 1 | Egg | 0 | - | |
| 2 | Larva | 50 | - | choose your game |
| 3 | Pupa | 150 | 35% | focus on a subfamily |
| 4 | Teneral | 400 | 50% | focus on a tribe |
| 5 | Beetle scout | 800 | 60% | focus on a genus |
| 6 | Field entomologist | 1500 | 70% | labels go to curators |
| 7 | Taxonomist | 3000 | 75% | |
| 8 | Beetle master | 6000 | 80% | |
| 9 | Coleopterist | 10000 | 85% | |
| 10 | King of Bark and Ambrosia | 25000 | 92% | |

**Badges on the leaderboard.** A small level badge next to each name (darker as the level rises, gold with a crown
at level 10) and a sparkle for players who found a new species.

**Less text.** The game home is a Play button, the level card, streak and today, four links and the top five
players. The unlocks page is the ladder and the focus form, with the expert rules behind a "How experts are
proven" toggle. The help page keeps the detail for those who want it.

## Update: partial credit, late validations, difficulty by skill, beta

**Partial credit** (`game_scoring.classify_truth`, `pair_truth`). Name That Beetle: every right rank earns its
weight (1/2/4/8); only the first wrong rank costs anything, `GAME_POINTS_OVERREACH` (35%) of its weight when a rank
above it was right (right genus, wrong species = 7 - 2.8 = 4.2, less than stopping at the genus, 7), or
`GAME_POINTS_WRONG_FACTOR` (75%) of everything claimed when even the subfamily is wrong. Family Ties: a cautious
answer that is true as far as it goes earns its rung's points; too close a tie earns the true rung's points minus
35% of the next rung's per rung too far; calling relatives strangers (or the reverse) loses 1 per step. Agreement
points on unvalidated pairs are partial per rung too, so they scale with the judges' strength the same way.

**Validated later** (`game_scoring.sync_late_truth`, `GameAnswer.validated_later`, `RetroCredit`). When a beetle
is validated after players answered it, their answers get `correct_*`/`ref_*` filled from the validated name and
count towards accuracy (`ratings`) and expertise (`skill_counts`) like any validated beetle; undone if the
validation is withdrawn. The first time such an answer is scored on the truth, a `RetroCredit` stores the points
before and after, the validated name and `bbox_validated_at`. The game home shows new ones once as "Checked since
you played" (photo crop, what you said, what it is, points, date); `/game/checked/` lists them all. Agreement
points never make a recap. Finishing a round now re-scores everyone who answered the same unvalidated beetles, so
agreement from a later expert reaches earlier players straight away, not only overnight.

**Difficulty by skill.** `target_difficulty` follows the player's rating (`GAME_DIFFICULTY_SKILL_WEIGHT` 0.7, start
0.15, +0.005 per round). Beetle difficulty still comes from how often other players get it right. Family Ties pairs
lean towards relations near the target (`RELATION_DIFFICULTY`: different subfamilies 0.1 ... same species 0.85),
so experts get close relatives and novices distant ones.

**Beta, sidebar, invitation, colour.** A Beta label on the game pages and in the sidebar. Signed-in users see their
username, level badge and score in the sidebar, linking to the game, and the home page invites everyone to play
(dismissible). The game has its own palette: level badges by tier (lime 1-3, sky 4-6, violet 7-9, gold 10), badge
accents, green/red points in recaps. The rest of the site keeps colour for meaning only.

## Update: report from the feed, superuser unlocks

**Report a photo from the feed.** Tapping a photo opens the full image; a faint cog in its corner opens a short
menu of reasons (name looks wrong, box doesn't fit, photo problem, something else). The report goes to the
curators like the round-review reports (`GameReport`, shown per ROI on the Image Annotation page, which also has a
"Reported by players" filter). Until a curator resolves the report, or validates the beetle after it, the beetle is
out of the game (`game.reported()` in both pools). The player moves on to the next beetle with no points lost
(`reported: true` holds that answer).

**Superusers can grant unlocks** (the Unlocks section of `/game/settings/`, `GamePreference.granted_perks`): any unlock, or all of
them, for any player, whatever their level. `game_levels.for_player` adds them to the earned ones, and a granted
"labels go to curators" also counts for `suggestion_voters`. The level itself is unchanged.

## Update: similarity first, rarity colours, standing, sharing, safe concurrency

**Game types.** The two games are called **Identification** (shown as "Name That Beetle" while playing) and
**Similarity**. New players start with Similarity only, the easier game; level 2 unlocks Identification and the
choice, and the default from then on is both. The game's name on the site comes from `GAME_DISPLAY_NAME`.

**Levels and streaks in RPG rarity colours:** grey 1-2, green 3-4, blue 5-6, purple 7-8, orange 9, gold 10 (King of
Bark and Ambrosia). Level 4 is now "Teneral". The streak flame goes grey, green (3+ days), blue (7+), purple (14+),
orange (30+), gold (100+). The play screen's top bar shows "L3 · 120/150" and a thin progress bar.

**Accuracy standing** (`game_board.accuracy_standing`): a histogram of every rated player's accuracy, the
average, a marker for you, your percentile and a tier by percentile: Common, Uncommon (25+), Rare (50+), Epic (75+),
Legendary (90+), Mythic (98+).

**Badges:** 21 new ones, including hard and specific ones (Year of the beetle: a 365-day streak; Flawless: 20 checked
beetles in a row all right; Taxonomic tourist: right species in 50 genera; Pinhole borer: 25 Platypodinae right;
Royalty: the top level), with accents by rarity.

**Sharing:** a large QR code on the game home pointing at the game home (`SITE_URL` in production), so anyone can
show the screen to a friend; scanning leads to sign-in or sign-up, then the game. The QR library (qrcodejs, MIT) is
served from `static/js/vendor`.

**Game settings page (Review game labels section):** a back link to the game, a button to Image Annotation, collapsible sections, and ROI links
that open Image Annotation on that image with the ROI selected (`?image=<id>&roi=<id>`).

**Many players at once.** A new answer adds to the player's total with a single UPDATE (`F()` + `Greatest`), so
simultaneous answers can't overwrite each other. Recomputes take a Postgres advisory lock for their writes, so two
players finishing at the same moment can't collide. Re-scoring the other players on the same beetles runs on the
Celery worker in production (`GAME_RECOMPUTE_IN_BACKGROUND`, on unless `DEBUG`), so finishing stays quick; if the
queue can't be reached it runs in the request instead.

## Update: game names (#496)

The owner renamed the games players see: Odd One Out is **Imposter Picker**, Select all is **Find Them All**, and the
choice that mixes every unlocked game (Mix, stored as `both`) is **All modes**. Name That Beetle is now
**Identification** everywhere, matching the toolbar. Only the labels changed: the stored modes (`odd`, `select`,
`both`, `mixed`, `classify`), URLs and perk keys stay, and migration 0050 only updates the model choice labels. In the
feed's toolbar, All modes sits apart from the four single games as its own dashed pill with a shuffle icon, so it reads
as "all of them mixed", not as a fifth game.

## Update: game wording (#538)

The owner's names, final for the full release: Imposter Picker is **Odd One Out** again and Identification is
**Naming** (identification means more than this game in the field); its experts are **Naming experts**, beside
Distinction experts. On a phone the toolbar says All, Similar, Odd one, Find all and Name. As in #496 only labels
changed (migration 0053); stored modes, URLs and perk keys stay.

- Find Them All says "Select every X"; Odd One Out asks "Find the one that doesn't share the same <rank>" ("Find
  the 2 that don't ..." when a grid has several odd ones, from the item's `odds`, the number of odd ones).
- The "Seen before: have another go" tag on a retried beetle's photo is gone (it sat under the photo's own pills,
  and players know); the server still marks the item (`again`) and the review still says "Seen before".
- The photo rule in the help, the how-to page and the tips is just "A photo must show a good part of the beetle."
- The session recap leads back to the game's home ("Bark & Ambrosia Detective home").
- While the feed loads, "Finding beetles…" and "Building your gallery…" take turns every 2.5 s; after 10 s it
  says "This is taking a while: probably making frass…". With reduced motion the lines don't alternate.
- The game is out of beta: the Beta pills (sidebar, game home, how-to page, loading screen, landing page) are gone.

## Update: one colour scale instead of rarity colours (#572)

Everything rated from worst to best uses one scale (`game_scale.py`, the `.scale-*` classes in `input.css`):
grey "Not yet", then red "Fair", orange "Decent", yellow "Good", green "Great" and deep green "Excellent". Blue is
kept for IBBI-AI and purple for the players' consensus; neither is ever on the scale.

- **Levels:** grey 1, light and full red 2-3, orange 4-5, yellow 6-7, green 8-9, glowing deep green 10.
- **Streak flame:** grey under 3 days, red 3+, orange 7+, yellow 14+, green 30+, deep green 100+.
- **Accuracy and challenge** (recap, report): under 30% red, 30-49% orange, 50-69% yellow, 70-84% green, 85%+ deep green.
- **Accuracy standing** by percentile: Fair, Decent (25+), Good (50+), Great (75+), Excellent (90+).
- **Badges:** the easiest red, then orange, yellow, green, and the two hardest (Royalty, Year of the beetle) glowing.
- **Expertise tree:** an empty grey marker for too few answers, red under 50%, orange, yellow and green in three steps
  up to what an expert needs, deep green for an expert (glowing for a Naming expert).
- **Confetti** says who agreed: green for correct on a checked beetle (with blue and purple when IBBI-AI and the
  players said the same), grey with blue or purple on an unchecked beetle IBBI-AI or the players agree on, glowing
  purple when a Naming expert did, a small grey pop for very few points. A new level is a pop-up in the middle of the
  screen in the level's colour, with gold and brown beetles and a sprinkle of that colour.

## Update: quicker between beetles, no label photos

- **No photos of labels.** A photo whose aspect mentions "label" is never in the game: not as a beetle to answer and
  not among a beetle's other photos (`game.LABEL_PHOTO`, in `playable_rois` and `specimen_photos`).
- **The review comes first, always.** An answer (other than a skip) replies with its review at once. Whatever comes
  next, the next beetle of the batch, the beetles a batch started small still gets, the next batch or the end of the
  feed, comes from `game_item`, which the page asks for while the review is read (`game_views._carry_on`).
- **A step change applies from the grid after next.** The next grid's photos are already loaded, so it shows as it was
  built; `restep` builds the later ones again at the new step.
- **Big grids load small.** In grids of 16 and 25 beetles only the small crops are cut and loaded ahead; a tile loads
  its sharp crop when it is zoomed (`game_crops.BIG_GRID`, `sizes_ahead`, the item's `sharp_on_zoom`).

## Update: speed work, play unchanged

None of this changes what a player sees or how the game plays; it changes when and where the work is done.

- **Every small crop is cut ahead.** A sweep on the heavy worker cuts the small crop of each playable beetle that has
  none (`game_crops.precut`), queued by a new batch at most every 6 hours, 100 crops a part. Large crops are still cut
  per batch.
- **Batches for the other games are built on the heavy worker** (`warm_game_batches_task`), the game played now
  first. They are only a head start; on the quick worker they held up the batches a player was waiting on.
- **A batch of grids reads its beetles once** (`game._Pool`, the open pool's ids in `_Grids.open_ids`) and answers the
  grids' questions in memory, by the same tests and the same random walk as the queries they replace.
- **Back draws from the crops.** The review names the crops the feed showed (`small`, `large`, `sharp_on_zoom`), so
  Back draws them like the feed instead of downloading the whole photos.
- **Timing lines reach the server log** (`LOGGING` in settings: one line per feed request; `LOG_GAME_TIMINGS=0`
  turns them off).

## Update: the rule of 3 for seeing a beetle again

A beetle whose names a player was shown (any answer's review names every beetle in it, with every other photo of the
same specimen) comes back to that player by the rule of 3: **3 minutes** after the first time it was shown, **3 hours**
after the second, then **3 days**, **3 weeks** and **3 months** (13 weeks), and every 3 months from then on, each wait
counted from the last time it was shown (`game.SEEN_AGAIN`, `held_back_ids`). This replaces "a later sitting and at
least `GAME_REVEAL_COOLDOWN_HOURS`" (that setting and `GAME_SESSION_GAP_MINUTES` are gone).

- Mistakes come back as retries on the same schedule (`game_relearn.due`), still at most `GAME_RETRY_MAX` tries and
  `GAME_RETRY_PER_BATCH` a batch, first in an easier game.
- The number of times shown is counted from the player's answers (`reveals`: one per answer, a specimen's showings
  for each of its photos).
- A chosen game that runs short takes back every beetle past its first 3-minute wait.
- Unchanged: such beetles come back in another game first; they earn full points but count towards accuracy and
  expertise only once they haven't been shown for `GAME_EXPERTISE_RECALL_DAYS`; unvalidated beetles a player has
  named or paired don't come back to them in that game.

## Update: naming skill counts upward

What a player recognises at a rank they recognise at the ranks above it: someone good at a tribe's genera is good at
that tribe and its subfamily too (`game_trust.skill_counts`, `grid_claims`).

- **Find Them All:** a tap counts at every rank of the group above the grid's own, filed under the tapped beetle's own
  taxon. It is right where the beetle shares the group's name there (an Ips tapped in a Xyleborus grid is wrong at
  genus and tribe, right at subfamily). A member left out says nothing about the ranks above.
- **Identification:** a right name counts at the ranks above it that the answer didn't give (the page fills them in
  when a name is picked, so this matters for answers sent without them), never at a rank the beetle has no name at.
  A wrong name implies nothing.
- Unchanged: each beetle counts once per rank; proving a skill still needs its children covered (#381); Similarity
  already judges every shared rank; Odd One Out (telling apart within a group) stays at its own rank. The player
  report may now show the higher names a grid gave the player. `recompute_game_scores` brings existing skills up to
  date at once (otherwise each player's catch up after their next batch).

## Update: the number to find varies

The owner: "the number you have to select can also vary so that it isn't always the same amount that you should
select." Each grid draws its own number when it is built (`game.odd_count`, `game.select_fewer`).

- **Odd One Out:** a step's odd ones are now the most its grids hide. `GAME_ODD_FEWER_SHARE` (0.5) of the grids at a
  step with several hide evenly fewer, never none: at 25 beetles and four odd ones, half hide four and the rest one,
  two or three. Steps with one odd one stay at one. The page asks for the grid's own number ("Which 2 are …"), and
  the server, the points (a grid is worth the same, split over its own odd ones), the review and the ladder all go
  by it.
- **Find Them All:** `GAME_SELECT_FEWER_SHARE` (0.3) of the grids hold evenly fewer AI beetles (down to none) and
  more validated beetles of other groups, so how many beetles belong to the group varies; beside fewer AI beetles the
  validated members reach the top of their range (4: 1–2, 9: 3–4, 16: 5–7, 25: 7–10). Validated non-members are still
  never fewer than members and every grid has a member to find, so neither tapping everything nor tapping nothing
  wins. Fewer AI beetles means fewer taps on them, hence the lower share; 0 turns either off.
