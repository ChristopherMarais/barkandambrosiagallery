"""
Scoring settings a superuser can see and tune on the Scoring page (/game/scoring/), without a deploy.

TUNABLES lists them, grouped as the page shows them, each with its default, limits and what it does. An override is a
GameTuning row; game.game_setting() reads overrides first, then settings.py, then the default in the code. Each
process keeps the overrides for OVERRIDE_SECONDS, so a change reaches every worker within half a minute. New answers
use it straight away; answers already given are re-scored when their player next leaves the game, every night, or at
once with "Re-score everyone".
"""
import time

OVERRIDE_SECONDS = 30
_cache = {"at": 0.0, "values": {}}


def _t(key, label, default, help, lo=0.0, hi=10.0, step=0.05, keys=None):
    return {"key": key, "label": label, "default": default, "help": help, "min": lo, "max": hi, "step": step,
            "keys": keys}


RANKS = ["subfamily", "tribe", "genus", "species"]
RUNGS = ["-1", "0", "1", "2", "3"]   # Family Ties depth: different subfamilies ... same species
RUNG_LABELS = {"-1": "Different subfamilies", "0": "Same subfamily", "1": "Same tribe", "2": "Same genus", "3": "Same species"}
GRID_SIZES = ["4", "9", "16", "25"]   # the grid games' sizes (#489)
PART_LABELS = dict(RUNG_LABELS, **{size: f"{size} beetles" for size in GRID_SIZES})

GROUPS = [
    ("Beetles we know (validated): what an answer earns", [
        _t("GAME_POINTS_RANK", "Points per rank named correctly", {"subfamily": 1.0, "tribe": 2.0, "genus": 4.0, "species": 8.0},
           "Naming: each rank named correctly earns its points (then times the Naming weight). Deeper "
           "ranks are worth more because they are harder and more useful.", 0, 50, 0.5, keys=RANKS),
        _t("GAME_POINTS_CLASSIFY_WEIGHT", "Naming weight", 3.0,
           "Every Naming answer counts this many times the points above, gains and losses alike: naming a "
           "beetle is the harder and more useful game.", 0.5, 10, 0.5),
        _t("GAME_PAIR_POINTS", "Similarity points by true relationship",
           {"-1": 1.0, "0": 2.0, "1": 4.0, "2": 7.0, "3": 12.0},
           "Similarity: the correct answer earns more the finer the line the player had to draw, each rung clearly "
           "more than the one above, and more so toward species. Odd One Out and Find Them All are priced from "
           "these too.", 0, 50, 0.5, keys=RUNGS),
        _t("GAME_POINTS_SIMILARITY_BONUS", "Bonus when the two photos look alike", 0.25,
           "Up to this share more when the two photos share photographer, place, magnification... (harder to tell "
           "apart), on an answer that is correct or cautious; never on a wrong one.", 0, 2),
        _t("GAME_POINTS_ODD_WEIGHT", "Odd One Out: correct pick", 1.5,
           "A correct pick earns this many times the Similarity points for how related the odd one is to the rest.", 0, 10),
        _t("GAME_POINTS_SELECT_WEIGHT", "Find Them All: a perfect grid", 1.25,
           "A perfect grid earns this many times the Similarity points for its rank, shared between the members.", 0, 10),
        _t("GAME_POINTS_RETRY_FACTOR", "A beetle seen again (retry)", 0.5,
           "A beetle the player got wrong, shown again to learn it, earns (and costs) this share of the points.", 0, 1),
    ]),
    ("Beetles we know (validated): what a mistake costs", [
        _t("GAME_POINTS_CONFIDENCE", "How sure a claim must be to pay", 0.7,
           "Every claim (a rank named, a Similarity rung, an Odd One Out pick, a Find Them All tap) costs "
           "k = t ÷ (1 − t) times what it earns when correct: 2⅓ at 0.7. So it pays on average only when the player is "
           "at least this sure, and below it stopping (or leaving a beetle untapped) earns more. Higher asks for more "
           "confidence and makes mistakes cost more.", 0.5, 0.95, 0.01),
    ]),
    ("Skipping and taking part", [
        _t("GAME_POINTS_UNSURE", "Skip in Naming and Similarity (cost)", 0.25,
           "A skip costs this little: less than any wrong answer, so not knowing is always better than guessing.", 0, 5),
        _t("GAME_POINTS_ODD_SKIP", "Skip in Odd One Out and Find Them All (earns)", 0.25,
           "In the grid games a skip earns this, to reward knowing when you don't know.", 0, 5),
        _t("GAME_POINTS_PARTICIPATION", "Taking part", 0.5,
           "Every real answer earns this on top, so the score grows with play; accuracy still decides most of it.", 0, 5),
    ]),
    ("Grid games: Odd One Out and Find Them All", [
        _t("GAME_GRID_SIZE_FACTOR", "Points by grid size", {"4": 1.0, "9": 1.5, "16": 2.0, "25": 2.5},
           "Every point of a grid, gained or lost, is times this for its number of beetles, on top of what its rank is "
           "worth: a bigger grid takes longer and is harder. Only grids from the ladder: older ones keep ×1.",
           0.5, 5, 0.25, keys=GRID_SIZES),
        _t("GAME_GRID_UP_AFTER", "Good grids in a row to go up a step", 2,
           "The grids grow from 4 to 9, 16 and 25 beetles, then go a rank deeper, from subfamily to species: 16 steps "
           "(Odd One Out 40: more odd ones in the bigger grids). A player goes up a step after this many good grids in "
           "a row and down one after a poor grid.", 1, 10, 1),
        _t("GAME_GRID_GOOD_SHARE", "Find Them All: share of the group to find", 0.75,
           "A Find Them All grid is good with no wrong tap and at least this share of the validated members found, poor "
           "when it lost points or found none. In Odd One Out the odd one found is good, a wrong pick poor. Skips "
           "are neither.", 0.25, 1, 0.05),
        _t("GAME_GRID_START_STEP", "Step a new player starts on", 1,
           "1 is 4 beetles at subfamily; 16 is 25 beetles at species in Find Them All (40 in Odd One Out), and a "
           "higher number stops at a game's last step. Every player has a step in each grid game.",
           1, 40, 1),
        _t("GAME_ODD_OPEN_SHARE_START", "Odd One Out: AI beetles among the rest at level 1", 0.25,
           "This share of the beetles that share the group are ones nobody has validated that IBBI-AI puts in it...",
           0, 1, 0.05),
        _t("GAME_ODD_OPEN_SHARE_END", "Odd One Out: AI beetles among the rest at the top level", 0.5,
           "...rising to this share at the top level. A pick on one is scored by agreement, never below zero.", 0, 1, 0.05),
        _t("GAME_AI_SURE_FROM", "IBBI-AI is sure from", 0.9,
           "When its predictions allow, every grid holds an AI beetle IBBI-AI is at least this sure of...", 0.5, 1, 0.01),
        _t("GAME_AI_UNSURE_BELOW", "IBBI-AI is unsure below", 0.6,
           "...and one it is less sure of than this. Where no group has both, grids are built without them.", 0, 1, 0.01),
    ]),
    ("Beetles nobody has validated yet", [
        _t("GAME_POINTS_CONSENSUS_CAP", "Most an agreed answer earns", 0.6,
           "An answer on an unvalidated beetle earns at most this share of what it would on a validated one, when "
           "strong players agree with it. A rank they disagree with costs k times as much, off the rest; never less "
           "than zero in all. Below 1, known beetles always pay more.", 0, 1),
        _t("GAME_POINTS_REFERENCE_CAP", "Matching Naming experts or a trusted model", 0.6,
           "An answer that matches what Naming experts (or a trusted model) say earns up to this share; a rank "
           "they name otherwise costs k times it, off the rest.", 0, 1),
        _t("GAME_RATER_MIN_JUDGED", "Answers on known beetles before a player can judge", 10,
           "Only players with at least this many answers on validated beetles, and a rating at or above the median, "
           "count as judges.", 1, 500, 1),
        _t("GAME_RATER_SPREAD", "How much stronger judges count", 0.1,
           "A judge counts by how their rating compares with yours (an Elo-like curve): smaller means stronger judges "
           "count far more than weaker ones.", 0.01, 1, 0.01),
        _t("GAME_REF_MODEL_MIN_CONFIDENCE", "Model: confidence to be a reference", 0.9,
           "The AI's name counts as a reference only when it is at least this sure about the beetle...", 0, 1, 0.01),
        _t("GAME_REF_MODEL_MIN_PRECISION", "Model: proven accuracy in that taxon", 0.95,
           "...and has been right this often about that very taxon on validated beetles...", 0, 1, 0.01),
        _t("GAME_REF_MODEL_MIN_CHECKED", "Model: sure calls checked in that taxon", 20,
           "...over at least this many of its sure calls there.", 1, 1000, 1),
    ]),
    ("Naming confidence: what a beetle is, and is not", [
        _t("GAME_SELECT_TAP_WEIGHT", "A Find Them All tap, against a name", 0.8,
           "Tapping an unvalidated beetle in Find Them All counts as this share of a direct identification towards its "
           "name (down to the grid's rank). Taps never make a Naming expert's verdict on their own.",
           0, 1, 0.05),
        _t("GAME_TIP_MIN_VOTES", "Players needed for a name tip", 3,
           "Curators see 'N reliable players say genus X' once at least this many players agree...", 1, 50, 1),
        _t("GAME_TIP_MIN_SUPPORT", "Share of the weighted vote for a tip", 0.75,
           "...and their name has at least this share of the reliability-weighted vote at that rank. The same share "
           "applies to 'not in' tips.", 0.5, 1, 0.05),
        _t("GAME_TIP_MIN_NOT_VOTES", "Players needed for a 'not in' tip", 2,
           "Curators see 'Players are confident it is not in genus X' once at least this many say so.", 1, 50, 1),
        _t("GAME_AUTO_APPLY_MIN_EXPERTS", "Naming experts who must agree to write a name in", 2,
           "Naming experts who must give the same species before it is written onto an unnamed beetle as "
           "an Expert ID (still unvalidated, for a curator to confirm).", 1, 10, 1),
    ]),
    ("Feeds: mistakes come back, hard beetles go to the easier games first", [
        _t("GAME_RETRY_PER_BATCH", "Mistakes back per batch", 2,
           "Up to this many validated beetles the player got wrong come back in each batch of the feed, spread "
           "through it: first in an easier game than the one they were missed in, then in that game. They come back "
           "by the rule of 3, like every beetle whose names were shown: 3 minutes after they were last shown, then "
           "3 hours, 3 days, 3 weeks and 3 months.", 0, 10, 1),
        _t("GAME_RETRY_MAX", "Tries at one mistake", 3,
           "A mistake stops coming back after this many tries, right or wrong.", 0, 20, 1),
        _t("GAME_EXPERTISE_RECALL_DAYS", "Gap after which a shown beetle counts again (days)", 30,
           "A beetle whose names a player was shown counts towards their accuracy, expertise and rating again "
           "once it hasn't been shown to them for this many days: naming it then is recall, not short-term "
           "memory. Points are the same either way.", 0, 365, 1),
        _t("GAME_HARD_FROM", "A beetle nobody has validated is hard from", 0.5,
           "Hard when players disagree on its genus this much, or IBBI-AI is this unsure (0 to 1); also when nobody "
           "could take it to species, or nobody has answered it and IBBI-AI has no confident call. Similarity shows "
           "hard beetles more.", 0, 1, 0.05),
        _t("GAME_ID_PLACED_SHARE", "Naming: share already placed", 0.5,
           "At least this share of the new beetles in Naming (when there are enough) are ones Similarity or "
           "a confident IBBI-AI has already put in a subfamily or tribe. Hard ones not placed yet wait for the "
           "easier games while there are others.", 0, 1, 0.05),
    ]),
    ("Naming experts (whose answers become trusted labels)", [
        _t("GAME_TRUST_MIN_ACCURACY", "Accuracy a Naming expert needs", 0.9,
           "In a taxon, a player must be correct at least this often on validated beetles. The same rules on "
           "telling its beetles apart make a Distinction expert, which unlocks nothing.", 0.5, 1, 0.01),
        _t("GAME_TRUST_IMAGES_PER_SPECIES", "Images that cover a species, genus or tribe", 5,
           "A Naming expert must have answered this many validated images of each child they cover (all of "
           "them for one with fewer): the species of a genus, the genera of a tribe, the tribes of a subfamily.",
           1, 100, 1),
        _t("GAME_TRUST_CHILDREN_SHARE", "Share of a taxon's children a Naming expert must cover", 0.75,
           "Rounded up, so a taxon with three or fewer children (at 75%) needs all of them. A rare genus no "
           "longer stops anyone becoming a Naming expert in a tribe.", 0.1, 1, 0.05),
        _t("GAME_TRUST_MIN_JUDGED", "Fewest answers in a taxon for a Naming expert", 10,
           "Also the fewest validated images a taxon needs before anyone can be proven in it.", 1, 500, 1),
        _t("GAME_EXPERT_PERCENTILE", "Naming experts come from the top share of players", 0.25,
           "Only the most reliable players overall (this share, by rating) are trusted as Naming experts.",
           0.01, 1, 0.01),
    ]),
    ("Difficulty: which beetles a player sees, and what they are worth", [
        _t("GAME_DIFFICULTY_START", "Where a new player starts", 0.15,
           "The difficulty (0 easy, 1 hard) a new player's beetles sit around.", 0, 1, 0.01),
        _t("GAME_DIFFICULTY_PER_ROUND", "Rise per finished batch", 0.005,
           "Added for every batch the player has finished, so it keeps creeping up.", 0, 0.1, 0.001),
        _t("GAME_DIFFICULTY_SKILL_WEIGHT", "How much reliability raises it", 0.7,
           "Times the player's rating (0 to 1): reliable players get harder beetles.", 0, 2, 0.05),
        _t("GAME_DIFFICULTY_MAX", "Hardest it goes", 0.9,
           "The target never goes above this.", 0, 1, 0.01),
        _t("GAME_DIFFICULTY_RECENT", "Recent answers looked at", 10,
           "The rating moves slowly, so the target also follows the player's last this many answers on validated "
           "beetles in the game they are playing (skips count as not correct).", 3, 100, 1),
        _t("GAME_DIFFICULTY_EASE_BELOW", "Ease off when fewer are correct than", 0.6,
           "When less than this share of those answers is correct, the next beetles are easier... Keep it below the "
           "confidence a claim needs, so players are eased back to where careful answers pay.", 0, 1, 0.05),
        _t("GAME_DIFFICULTY_EASE", "...by this much", 0.15,
           "...this much lower on the 0 to 1 scale (never below 0.05).", 0, 1, 0.01),
        _t("GAME_DIFFICULTY_PUSH_ABOVE", "Push up when more are correct than", 0.85,
           "When more than this share is correct, the next beetles are harder...", 0, 1, 0.01),
        _t("GAME_DIFFICULTY_PUSH", "...by this much", 0.05,
           "...this much higher (never above the hardest).", 0, 1, 0.01),
        _t("GAME_POINTS_DIFFICULTY_SPREAD", "Points by how hard the beetle is", 0.25,
           "Naming and Similarity: a gain is multiplied by 1 + spread × (2p − 1), where p is how hard the "
           "beetle is among all beetles (0 easiest, 1 hardest), and a loss by 1 − spread × (2p − 1). So the hardest "
           "pay up to this share more and cost this share less when missed. 0 turns it off.", 0, 0.95, 0.05),
    ]),
]
TUNABLES = {t["key"]: dict(t, group=g) for g, items in GROUPS for t in items}


def overrides():
    """{key: value} of the stored overrides, refreshed every OVERRIDE_SECONDS (empty before the table exists)."""
    now = time.monotonic()
    if now - _cache["at"] > OVERRIDE_SECONDS:
        try:
            from .models import GameTuning
            _cache["values"] = dict(GameTuning.objects.values_list("key", "value"))
        except Exception:   # before migrations, or a broken transaction: the defaults
            _cache["values"] = {}
        _cache["at"] = now
    return _cache["values"]


def forget():
    """Read the overrides again on next use (after a change in this process)."""
    _cache["at"] = 0.0


def current(key):
    from .game import game_setting
    t = TUNABLES[key]
    value = game_setting(key, t["default"])
    if t["keys"] and isinstance(value, dict):   # a part added since it was saved (a grid of 25) takes its default
        return {**t["default"], **value}
    return value


def clean(key, raw):
    """A submitted value checked against its limits: (value, error)."""
    t = TUNABLES[key]
    if t["keys"]:
        out = {}
        for k in t["keys"]:
            value, error = clean_number(t, (raw or {}).get(k))
            if error:
                return None, f"{t['label']} ({PART_LABELS.get(k, k)}): {error}"
            out[k] = value
        return out, None
    return clean_number(t, raw)


def clean_number(t, raw):
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None, "must be a number"
    if not t["min"] <= value <= t["max"]:
        return None, f"must be between {t['min']:g} and {t['max']:g}"
    if t["step"] >= 1:
        value = int(round(value))
    return value, None


def save(changes, user):
    """Store {key: value}; a value equal to its default removes the override. Returns the keys that changed."""
    from .models import GameTuning
    changed = []
    for key, value in changes.items():
        if value == current(key):
            continue
        if value == TUNABLES[key]["default"]:
            row = GameTuning.objects.filter(key=key).first()
            if row:
                row._change_reason = "Back to default"
                row.delete()
        else:
            GameTuning.objects.update_or_create(key=key, defaults={"value": value, "updated_by": user})
        changed.append(key)
    forget()
    return changed


# ---------------------------------------------------------------------------
# Worked examples and balance checks, from the current values
# ---------------------------------------------------------------------------
def numbers():
    v = {k: current(k) for k in TUNABLES}
    rank, pair = v["GAME_POINTS_RANK"], {int(k): p for k, p in v["GAME_PAIR_POINTS"].items()}
    w = v["GAME_POINTS_CLASSIFY_WEIGHT"]
    return v, rank, pair, w


def _classify(w, named, right):
    """Naming points for naming the first ``named`` ranks, the first ``right`` of them correctly."""
    from .game_scoring import classify_points

    return sum(classify_points({r: i < right for i, r in enumerate(RANKS[:named])}).values()) * w


def _grid_worth(weight, pair, rank, size, ladder=True):
    """A correct Odd One Out pick, or a perfect Find Them All grid, of ``size`` beetles at ``rank``."""
    factor = float(current("GAME_GRID_SIZE_FACTOR")[str(size)]) if ladder else 1.0
    return weight * pair[RANKS.index(rank) - 1] * factor


def examples():
    """[(game, what, points)] for typical answers, so the effect of a change is easy to see."""
    from .game_scoring import pair_points, select_points, wrong_cost

    v, rank, pair, w = numbers()
    k, cap = wrong_cost(), v["GAME_POINTS_CONSENSUS_CAP"]
    odd_w, sel_w = v["GAME_POINTS_ODD_WEIGHT"], v["GAME_POINTS_SELECT_WEIGHT"]
    sel9 = _grid_worth(sel_w, pair, "species", 9)
    rows = [
        ("Naming", "Species correct (every rank)", _classify(w, 4, 4)),
        ("Naming", "Stopped at a correct genus", _classify(w, 3, 3)),
        ("Naming", "Correct genus, wrong species", _classify(w, 4, 3)),
        ("Naming", "Correct subfamily only", _classify(w, 1, 1)),
        ("Naming", "Correct subfamily, wrong tribe", _classify(w, 2, 1)),
        ("Naming", "Wrong subfamily, nothing more named", _classify(w, 1, 0)),
        ("Naming", "Wrong subfamily, claimed down to species", _classify(w, 4, 0)),
        ("Naming", "Species agreed by strong players (unvalidated beetle, most)", _classify(w, 4, 4) * cap),
        ("Similarity", "Correct: same species", pair_points(3, 3)[0]),
        ("Similarity", "Correct: same genus", pair_points(2, 2)[0]),
        ("Similarity", "Correct: different subfamilies", pair_points(-1, -1)[0]),
        ("Similarity", "'Same tribe' for two of one genus (cautious, true)", pair_points(1, 2)[0]),
        ("Similarity", "'Same genus' for two of one tribe (one rung too close: wrong)", pair_points(2, 1)[0]),
        ("Similarity", "'Same genus' for two of different subfamilies (wrong)", pair_points(2, -1)[0]),
        ("Similarity", "'Different subfamilies' for two of one genus (wrong)", pair_points(-1, 2)[0]),
        ("Odd One Out", "Correct pick, species round, 4 beetles", _grid_worth(odd_w, pair, "species", 4)),
        ("Odd One Out", "Correct pick, species round, 16 beetles", _grid_worth(odd_w, pair, "species", 16)),
        ("Odd One Out", "Correct pick, species round, 25 beetles", _grid_worth(odd_w, pair, "species", 25)),
        ("Odd One Out", "Wrong pick, subfamily round, 4 beetles", -k * _grid_worth(odd_w, pair, "subfamily", 4)),
        ("Odd One Out", "Wrong pick, species round, 4 beetles", -k * _grid_worth(odd_w, pair, "species", 4)),
        ("Find Them All", "Perfect grid, species, 9 beetles", sel9),
        ("Find Them All", "Perfect grid, species, 16 beetles", _grid_worth(sel_w, pair, "species", 16)),
        ("Find Them All", "Perfect grid, species, 25 beetles", _grid_worth(sel_w, pair, "species", 25)),
        ("Find Them All", "3 of 3 found plus one wrong tap, species, 9 beetles", select_points(sel9, 3, 3, 1)),
        ("Find Them All", "One wrong tap, species, 9 beetles with 3 to find", select_points(sel9, 3, 0, 1)),
        ("Find Them All", "The most a grid can lose, species, 9 beetles", -sel9),
        ("Find Them All", "Perfect grid, species, 9 beetles, from before the ladder (×1)",
         _grid_worth(sel_w, pair, "species", 9, ladder=False)),
        ("Any game", "Skip (Naming, Similarity)", -v["GAME_POINTS_UNSURE"]),
        ("Any game", "Skip (Odd One Out, Find Them All)", v["GAME_POINTS_ODD_SKIP"]),
        ("Any game", "Taking part (added to every real answer)", v["GAME_POINTS_PARTICIPATION"]),
    ]
    return [(game, what, round(p, 2)) for game, what, p in rows]


DIFFICULTY_EXAMPLES = [("Easy", 0.1), ("Middling", 0.5), ("Hard", 0.9)]   # harder than 10%, 50%, 90% of beetles


def _break_even(claim, stop):
    """
    The chance of being right above which a claim earns more on average than not making it. ``claim`` is what the
    claim earns when right and when wrong, ``stop`` what not making it earns in the same two cases.
    """
    gain, loss = claim[0] - stop[0], stop[1] - claim[1]
    return loss / (gain + loss) if gain + loss > 0 else 1.0


def thresholds():
    """
    [(game, decision, deeper, [chance per DIFFICULTY_EXAMPLES column])]: how sure a player must be for each claim to
    pay on average, on an easy, a middling and a hard beetle (the grid games aren't priced by difficulty). ``deeper``
    marks a claim one step further than a true answer (a rank, a rung, a tap); the others are answering at all
    against a skip, where the point for taking part counts too.
    """
    from .game_scoring import by_difficulty, pair_points, wrong_cost

    v, rank, pair, w = numbers()
    s, part, k = v["GAME_POINTS_DIFFICULTY_SPREAD"], v["GAME_POINTS_PARTICIPATION"], wrong_cost()
    skip, grid_skip = -v["GAME_POINTS_UNSURE"], v["GAME_POINTS_ODD_SKIP"]
    ms = [1 + s * (2 * p - 1) for _, p in DIFFICULTY_EXAMPLES]
    rows = []

    def row(game, decision, claim, stop, answering=False, scaled=True):
        cells = []
        for m in ms if scaled else [1.0] * len(ms):
            c = [by_difficulty(x, m) + (part if answering else 0.0) for x in claim]
            cells.append(round(_break_even(c, [by_difficulty(x, m) for x in stop]), 3))
        rows.append((game, decision, not answering, cells))

    row("Naming", "Name the subfamily, or skip", (_classify(w, 1, 1), _classify(w, 1, 0)), (skip, skip), True)
    for i in range(1, len(RANKS)):
        row("Naming", f"Name the {RANKS[i]} too", (_classify(w, i + 1, i + 1), _classify(w, i + 1, i)),
            (_classify(w, i, i),) * 2)
    row("Similarity", "Say 'same subfamily', or skip", (pair_points(0, 0)[0], pair_points(0, -1)[0]), (skip, skip), True)
    for d in range(1, len(RANKS)):
        row("Similarity", f"Say '{RUNG_LABELS[str(d)].lower()}', not one rung less",
            (pair_points(d, d)[0], pair_points(d, d - 1)[0]), (pair_points(d - 1, d)[0], pair_points(d - 1, d - 1)[0]))
    for rank_name, size in (("subfamily", 4), ("species", 25)):
        worth = _grid_worth(v["GAME_POINTS_ODD_WEIGHT"], pair, rank_name, size)
        row("Odd One Out", f"Pick, or skip ({rank_name}, {size} beetles)", (worth, -k * worth),
            (grid_skip, grid_skip), True, scaled=False)
    row("Find Them All", "Tap one more beetle", (1.0, -k), (0.0, 0.0), scaled=False)
    return rows


# How careful players of three skills fare, to check that no game is the way to farm points (#530). The chances are
# how often each is right naming a rank (Naming) or telling two beetles apart at it (the other games, a little
# easier); a careful player claims only what they are at least GAME_POINTS_CONFIDENCE sure of. Their grids are at the
# step the ladder settles them on. Seconds per answer are estimates, not measurements: Naming 6 plus 6 per rank
# named; Similarity 10, 8 and 7; a grid by its size.
PLAYERS = [
    ("Novice", (0.80, 0.55, 0.35, 0.20), (0.88, 0.73, 0.61, 0.52), ("subfamily", 9), 10),
    ("Average", (0.95, 0.85, 0.72, 0.50), (0.97, 0.91, 0.83, 0.70), ("genus", 9), 8),
    ("Strong", (0.99, 0.96, 0.90, 0.78), (0.99, 0.98, 0.94, 0.87), ("species", 25), 7),
]
GRID_SECONDS = {"odd": {4: 8, 9: 12, 16: 18, 25: 26}, "select": {4: 10, 9: 18, 16: 28, 25: 40}}
CAREFUL_SLIP = 0.4    # a careful player taps a non-member this share as often as a blind tapper would
FARM_BAND = 2.0       # per player, no game earns more than this many times another per minute
GAMES = [("classify", "Naming"), ("pair", "Similarity"), ("odd", "Odd One Out"), ("select", "Find Them All")]


def _pair_walk(telling, t, truth, value, r=0, said=None, chance=1.0):
    """
    Expected Similarity points for a pair whose true rung is ``truth``: rank by rank, while at least ``t`` sure, the
    player judges whether the two share it (right with chance telling[r]) and stops at the first "no".
    """
    if r == len(RANKS) or telling[r] < t:
        return chance * value(said, truth)
    shared = telling[r] if r <= truth else 1 - telling[r]   # the chance they are judged to share it
    stop = said if said is not None else -1                  # "no" at the subfamily: "different subfamilies"
    return _pair_walk(telling, t, truth, value, r + 1, r, chance * shared) + chance * (1 - shared) * value(stop, truth)


def expected_play():
    """
    {"players": [name], "rows": [(game, [(points per answer, points per minute) per player])], "band": [best ÷ worst
    per minute, per player]}: careful play on validated beetles of middling difficulty, first tries, the point for
    taking part included.
    """
    from .game import SELECT_AI, SELECT_MEMBERS
    from .game_scoring import confidence, pair_points, select_points, wrong_cost

    v, rank, pair, w = numbers()
    t, k, part = confidence(), wrong_cost(), v["GAME_POINTS_PARTICIPATION"]
    unsure, grid_skip = v["GAME_POINTS_UNSURE"], v["GAME_POINTS_ODD_SKIP"]

    def value(said, truth):   # a Similarity answer's points, or a skip's
        return -unsure if said is None else pair_points(said, truth)[0] + part

    cells = {key: [] for key, _ in GAMES}
    for naming, telling, (grid_rank, size), pair_seconds in (p[1:] for p in PLAYERS):
        named = next((i for i, q in enumerate(naming) if q < t), len(naming))
        chances = [1.0, *naming[:named], 0.0]
        ev = sum((chances[j] - chances[j + 1]) * _classify(w, named, j) for j in range(named + 1)) + part if named \
            else -unsure
        cells["classify"].append((ev, 6 + 6 * named))
        total = sum(_pair_walk(telling, t, truth, value) for truth in range(-1, len(RANKS)))
        cells["pair"].append((total / (len(RANKS) + 1), pair_seconds))

        p = telling[RANKS.index(grid_rank)]
        worth = _grid_worth(v["GAME_POINTS_ODD_WEIGHT"], pair, grid_rank, size)
        cells["odd"].append((p * worth - (1 - p) * k * worth + part if p >= t else grid_skip, GRID_SECONDS["odd"][size]))
        members, ai = sum(SELECT_MEMBERS[size]) / 2, sum(SELECT_AI[size]) / 2
        worth = _grid_worth(v["GAME_POINTS_SELECT_WEIGHT"], pair, grid_rank, size)
        grid = select_points(worth, members, members * p, (size - members - ai) * (1 - p) * CAREFUL_SLIP) + part
        cells["select"].append((grid if p >= t else grid_skip, GRID_SECONDS["select"][size]))
    rows = [(label, [(round(ev, 2), round(ev * 60 / seconds, 1)) for ev, seconds in cells[key]]) for key, label in GAMES]
    band = []
    for i in range(len(PLAYERS)):
        per_minute = [cells[key][i][0] * 60 / cells[key][i][1] for key, _ in GAMES]
        band.append(round(max(per_minute) / min(per_minute), 2) if min(per_minute) > 0 else float("inf"))
    return {"players": [p[0] for p in PLAYERS], "rows": rows, "band": band}


def odd_guess(size, odds=1):
    """
    What blind picks in an Odd One Out grid of ``size`` hiding ``odds`` odd ones are worth on average, as a share of
    finding them all (#540): each pick is an odd one ``odds`` times in ``size`` and earns a share, is a validated
    beetle of the rest otherwise and costs k shares, or an AI beetle and earns nothing (scored by agreement, never below
    zero). The grid with the most AI beetles a player meets, so the fewest wrong picks, is the test.
    """
    from .game import odd_open_count
    from .game_levels import LEVELS
    from .game_scoring import wrong_cost

    ai = min(size - odds - 1, max(2, odd_open_count(len(LEVELS), size, odds)))
    return (odds - wrong_cost() * (size - odds - ai)) / size


def select_tap_all(size):
    """
    What tapping every beetle of a Select all grid of ``size`` is worth, as a share of a perfect grid: every validated
    member correct, every validated non-member wrong, AI beetles nothing. The grid with the most members for its
    non-members is the test.
    """
    from .game import SELECT_AI, SELECT_MEMBERS
    from .game_scoring import wrong_cost

    low, high = SELECT_MEMBERS[size]
    wrong = wrong_cost()
    return max((m - wrong * (size - m - a)) / m for m in range(low, high + 1)
               for a in range(SELECT_AI[size][1] + 1) if size - m - a >= m)


def _wrong_answers(v, pair, w):
    """[(what, points)]: the cheapest wrong answer of each kind, before the point for taking part."""
    from .game_scoring import pair_points, wrong_cost

    out = [(f"Naming: wrong {RANKS[i]}" + (" after correct ranks" if i else ""), _classify(w, i + 1, i))
           for i in range(len(RANKS))]
    out += [(f"Similarity: '{RUNG_LABELS[str(d + 1)].lower()}' for {RUNG_LABELS[str(d)].lower()}",
             pair_points(d + 1, d)[0]) for d in range(-1, len(RANKS) - 1)]
    out.append(("Similarity: 'different subfamilies' for one subfamily", pair_points(-1, 0)[0]))
    out.append(("Odd One Out: wrong pick, subfamily round, 4 beetles",
                -wrong_cost() * _grid_worth(v["GAME_POINTS_ODD_WEIGHT"], pair, "subfamily", 4)))
    return out


def checks():
    """[(rule, ok, why)]: does the current tuning still push players towards accurate, careful, precise answers?"""
    from .game_scoring import confidence, pair_points

    v, rank, pair, w = numbers()
    t, part, unsure = confidence(), v["GAME_POINTS_PARTICIPATION"], v["GAME_POINTS_UNSURE"]
    steps = [rank[r] for r in RANKS]
    rungs = [pair[d] for d in range(-1, len(RANKS))]
    rises = [b - a for a, b in zip(rungs[1:], rungs[2:])]
    specific = all(b > a for a, b in zip(steps, steps[1:])) and all(b > a for a, b in zip(rungs, rungs[1:])) \
        and all(b >= a for a, b in zip(rises, rises[1:]))
    limits = thresholds()
    deeper = [(game, what, cells) for game, what, is_deeper, cells in limits if is_deeper]
    answering = [(game, what, cells) for game, what, is_deeper, cells in limits if not is_deeper]
    middle = [c[1] for _, _, c in deeper]
    wrongs = _wrong_answers(v, pair, w)
    worst_wrong = max(wrongs, key=lambda x: x[1])
    overclaims_ok = all(pair_points(d, truth)[0] < min(0.0, pair_points(truth, truth)[0])
                        for truth in range(-1, len(RANKS)) for d in range(truth + 1, len(RANKS)))
    cautious_ok = all(0 <= pair_points(d, truth)[0] < pair_points(truth, truth)[0]
                      for truth in range(len(RANKS)) for d in range(truth))
    sizes = [int(s) for s in GRID_SIZES]
    from .game_grid_ladder import ODD_SHAPES

    guesses, taps = {shape: odd_guess(*shape) for shape in ODD_SHAPES}, {n: select_tap_all(n) for n in sizes}
    factor = [v["GAME_GRID_SIZE_FACTOR"][s] for s in GRID_SIZES]
    per_size = lambda values: ", ".join(f"{n} beetles {e:+.2f}" for n, e in values.items())   # noqa: E731
    play = expected_play()
    top_grid = max(_grid_worth(v["GAME_POINTS_ODD_WEIGHT"], pair, "species", 25),
                   _grid_worth(v["GAME_POINTS_SELECT_WEIGHT"], pair, "species", 25))
    top_pair = pair[3] * (1 + v["GAME_POINTS_SIMILARITY_BONUS"])
    return [
        ("Precise beats vague", specific,
         "Each rank and each Similarity rung is worth more than the one above it, and the rungs rise more steeply "
         "toward species, so a correct deeper answer always pays clearly more than stopping above it."),
        (f"Going deeper pays only from {t:.0%} sure", min(middle) >= t - 0.005 and min(min(c) for _, _, c in deeper) >= 0.5,
         "How sure a player must be for one more rank, rung or tap to pay on a middling beetle: "
         + "; ".join(f"{g} {what.lower()} {c[1]:.0%}" for g, what, c in deeper)
         + ". Never below 50% on an easy or hard beetle either."),
        ("Answering pays only when more likely right than not", min(c[1] for _, _, c in answering) >= 0.5,
         "Against a skip, with the point for taking part, on a middling beetle (easy to hard in brackets): "
         + "; ".join(f"{g} {what.lower()} {c[1]:.0%} ({max(c):.0%} to {min(c):.0%})" for g, what, c in answering)
         + "."),
        ("A claim beyond the truth is wrong, never partly right", overclaims_ok,
         "Saying two beetles are closer relatives than they are (or related when they aren't) scores below zero and "
         "below the true answer; the round review and the card after each answer call it wrong."),
        ("Cautious answers earn something, less than the precise one", cautious_ok,
         "'Same tribe' for two of one genus is true as far as it goes: it earns the tribe's points, never a loss, and "
         "less than 'same genus'."),
        ("A mistake costs more than a skip", worst_wrong[1] + part < -unsure,
         f"The cheapest mistake ({worst_wrong[0]}) scores {worst_wrong[1]:+.2f}, {worst_wrong[1] + part:+.2f} with the "
         f"point for taking part; a skip costs {unsure:g}."),
        ("A blind guess in Odd One Out loses on average", all(e < 0 for e in guesses.values()),
         "At every step of its ladder, each pick right as often as the odd ones are among the beetles and wrong on each "
         "validated beetle of the rest: expected "
         + ", ".join(f"{n} beetles, {k} odd {e:+.2f}" for (n, k), e in guesses.items())
         + " × what finding them all earns."),
        ("Tapping everything in Find Them All loses", all(e < 0 for e in taps.values()),
         f"Non-members are never fewer than members, so a wrong tap must cost more than a member earns: expected "
         f"{per_size(taps)} × a perfect grid."),
        ("No game is the way to farm points", all(b <= FARM_BAND for b in play["band"]),
         "Careful players earn per minute within ×" + f"{FARM_BAND:g} across the four games: "
         + ", ".join(f"{name} ×{b:.2f}" for name, b in zip(play["players"], play["band"])) + " (see the table above)."),
        ("Harder tasks pay more per answer", _classify(w, 4, 4) > top_grid > top_pair,
         f"A species named ({_classify(w, 4, 4):g}) > the best grid ({top_grid:g}) > the best Similarity answer "
         f"({top_pair:g})."),
        ("Bigger grids are worth at least as much", factor == sorted(factor),
         "Points by grid size must not fall as the grids grow, or a player would do better to stay small."),
        ("The difficulty eases off before careful answers stop paying",
         v["GAME_DIFFICULTY_EASE_BELOW"] < t < v["GAME_DIFFICULTY_PUSH_ABOVE"],
         f"Beetles get easier below {v['GAME_DIFFICULTY_EASE_BELOW']:.0%} correct and harder above "
         f"{v['GAME_DIFFICULTY_PUSH_ABOVE']:.0%}, around the {t:.0%} a claim needs: players are kept where careful "
         "play pays and they still learn."),
        ("A second try pays less than the first", 0 < v["GAME_POINTS_RETRY_FACTOR"] < 1,
         "A beetle shown again to learn it earns (and costs) a share of the points, the same share both ways, so the "
         "confidence a claim needs is the same."),
        ("IBBI-AI's sure calls are surer than its unsure ones", v["GAME_AI_SURE_FROM"] >= v["GAME_AI_UNSURE_BELOW"],
         "A grid's sure AI beetle and its unsure one come from bands that must not overlap."),
        ("Known beetles pay more than agreement on unknown ones",
         v["GAME_POINTS_CONSENSUS_CAP"] < 1 and v["GAME_POINTS_REFERENCE_CAP"] < 1,
         "Points on validated beetles are the real test of accuracy; agreement is capped below them."),
        ("Taking part earns less than a correct subfamily", part < rank["subfamily"] * w,
         "So the score follows accuracy, not just the number of answers."),
        ("A tap counts less than a name", v["GAME_SELECT_TAP_WEIGHT"] < 1,
         "Tapping a beetle among nine is a quicker, weaker judgement than naming it."),
        ("Naming experts must be very accurate", v["GAME_TRUST_MIN_ACCURACY"] >= 0.85,
         "Naming experts' labels reach curators as trusted: below 85% that trust is not earned."),
        *difficulty_checks(v, rank, pair, w),
    ]


def difficulty_checks(v, rank, pair, w):
    """
    Points by difficulty (#492) scale a whole answer by one factor, so a wrong answer stays a loss at any difficulty.
    What can still break: on the hardest beetles a mistake costs only (1 − spread) of its usual amount, while the
    point for taking part stays the same. (How sure a claim must be there is in thresholds().)
    """
    s = v["GAME_POINTS_DIFFICULTY_SPREAD"]
    cheapest = -max(p for _, p in _wrong_answers(v, pair, w))
    hardest = (1 - s) * cheapest
    return [
        ("Points by difficulty never make a mistake free", 0 <= s < 1,
         f"The hardest beetles pay ×{1 + s:.2f} and a mistake there costs ×{1 - s:.2f}; the easiest the reverse. "
         "The spread must stay below 1."),
        ("A careless answer still loses on the hardest beetles", hardest > v["GAME_POINTS_PARTICIPATION"],
         f"The cheapest wrong answer costs {hardest:.2f} there, against +{v['GAME_POINTS_PARTICIPATION']:g} for "
         "taking part: it must cost more, or careless answers would pay."),
    ]


def difficulty_examples():
    """
    {"columns": [(name, p, gain ×, loss ×)], "rows": [(game, what, [points per column])]}: the multiplier for an easy, middling
    and hard beetle, and what it does to typical answers (before the point for taking part).
    """
    from .game_scoring import by_difficulty, pair_points

    v, rank, pair, w = numbers()
    s = v["GAME_POINTS_DIFFICULTY_SPREAD"]
    columns = [(name, p, 1 + s * (2 * p - 1)) for name, p in DIFFICULTY_EXAMPLES]
    base = [
        ("Naming", "Species correct (every rank)", _classify(w, 4, 4)),
        ("Naming", "Correct genus, wrong species", _classify(w, 4, 3)),
        ("Naming", "Wrong subfamily, claimed down to species", _classify(w, 4, 0)),
        ("Similarity", "Correct: same genus", pair_points(2, 2)[0]),
        ("Similarity", "'Different subfamilies' for two of one genus", pair_points(-1, 2)[0]),
        ("Both", "Skip", -v["GAME_POINTS_UNSURE"]),
    ]
    rows = [(game, what, [round(by_difficulty(p, m), 2) for _, _, m in columns]) for game, what, p in base]
    return {"columns": [(name, p, round(m, 2), round(2 - m, 2)) for name, p, m in columns], "rows": rows}
