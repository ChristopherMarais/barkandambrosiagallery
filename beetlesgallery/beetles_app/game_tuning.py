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
GRID_SIZES = ["4", "9", "16"]   # the grid games' sizes (#489)
PART_LABELS = dict(RUNG_LABELS, **{size: f"{size} beetles" for size in GRID_SIZES})

GROUPS = [
    ("Beetles we know (validated): what an answer earns", [
        _t("GAME_POINTS_RANK", "Points per rank named correctly", {"subfamily": 1.0, "tribe": 2.0, "genus": 4.0, "species": 8.0},
           "Identification: each rank named correctly earns its points (then times the Identification weight). Deeper "
           "ranks are worth more because they are harder and more useful.", 0, 50, 0.5, keys=RANKS),
        _t("GAME_POINTS_CLASSIFY_WEIGHT", "Identification weight", 3.0,
           "Every Identification answer counts this many times the points above, gains and losses alike: naming a "
           "beetle is the harder and more useful game.", 0.5, 10, 0.5),
        _t("GAME_PAIR_POINTS", "Similarity points by true relationship", {"-1": 1.0, "0": 2.0, "1": 3.0, "2": 5.0, "3": 5.0},
           "Similarity: the correct answer earns more the finer the line the player had to draw. Odd One Out and "
           "Select all are priced from these too.", 0, 50, 0.5, keys=RUNGS),
        _t("GAME_POINTS_SIMILARITY_BONUS", "Bonus when the two photos look alike", 0.25,
           "Up to this share more when the two photos share photographer, place, magnification... (harder to tell apart).",
           0, 2),
        _t("GAME_POINTS_ODD_WEIGHT", "Odd One Out: correct pick", 1.5,
           "A correct pick earns this many times the Similarity points for how related the odd one is to the rest.", 0, 10),
        _t("GAME_POINTS_SELECT_WEIGHT", "Select all: a perfect grid", 2.0,
           "A perfect grid earns this many times the Similarity points for its rank, shared between the members.", 0, 10),
        _t("GAME_POINTS_RETRY_FACTOR", "A beetle seen again (retry)", 0.5,
           "A beetle the player got wrong, shown again to learn it, earns this share of the points.", 0, 1),
    ]),
    ("Beetles we know (validated): what a mistake costs", [
        _t("GAME_POINTS_OVERREACH", "Going one rank too far", 0.35,
           "After correct ranks, the first wrong one costs this share of its own points (a correct genus with a wrong "
           "species earns less than stopping at the genus). In Similarity, each rung claimed too close costs this "
           "share of the next rung's points.", 0, 2),
        _t("GAME_POINTS_WRONG_FACTOR", "Wrong from the start (Identification)", 0.75,
           "A wrong subfamily, with nothing correct, costs this share of everything the player claimed, so a confident "
           "wrong species costs most.", 0, 3),
        _t("GAME_POINTS_PAIR_STEP", "Similarity: related vs unrelated mixed up", 1.0,
           "Calling related beetles 'different subfamilies', or unrelated ones related, costs this per step it is off.",
           0, 10, 0.25),
        _t("GAME_POINTS_ODD_WRONG_FACTOR", "Odd One Out: wrong pick", 1.25,
           "A wrong pick costs this many times what a correct one earns. Above 1/3 (four beetles) a blind guess loses "
           "on average.", 0, 5),
        _t("GAME_POINTS_SELECT_WRONG", "Select all: tapping a beetle that doesn't belong", 1.5,
           "Each wrong tap costs this many members' shares; a member left out costs nothing. Above 1, tapping "
           "everything loses.", 0, 5),
    ]),
    ("Skipping and taking part", [
        _t("GAME_POINTS_UNSURE", "Skip in Identification and Similarity (cost)", 0.25,
           "A skip costs this little: less than any wrong answer, so not knowing is always better than guessing.", 0, 5),
        _t("GAME_POINTS_ODD_SKIP", "Skip in Odd One Out and Select all (earns)", 0.25,
           "In the grid games a skip earns this, to reward knowing when you don't know.", 0, 5),
        _t("GAME_POINTS_PARTICIPATION", "Taking part", 0.5,
           "Every real answer earns this on top, so the score grows with play; accuracy still decides most of it.", 0, 5),
    ]),
    ("Grid games: Odd One Out and Select all", [
        _t("GAME_GRID_SIZE_FACTOR", "Points by grid size", {"4": 1.0, "9": 1.5, "16": 2.0},
           "Every point of a grid, gained or lost, is times this for its number of beetles, on top of what its rank is "
           "worth: a bigger grid takes longer and is harder.", 0.5, 5, 0.25, keys=GRID_SIZES),
        _t("GAME_GRID_UP_AFTER", "Good grids in a row to go up a step", 2,
           "The grids grow from 4 to 9 to 16 beetles, then go a rank deeper, from subfamily to species: 12 steps. A "
           "player goes up a step after this many good grids in a row and down one after a poor grid.", 1, 10, 1),
        _t("GAME_GRID_GOOD_SHARE", "Select all: share of the group to find", 0.75,
           "A Select all grid is good with no wrong tap and at least this share of the validated members found, poor "
           "with more wrong taps than right ones or none right. In Odd One Out the odd one found is good, a wrong pick "
           "poor. Skips are neither.", 0.25, 1, 0.05),
        _t("GAME_GRID_START_STEP", "Step a new player starts on", 1,
           "1 is 4 beetles at subfamily, 12 is 16 beetles at species. Every player has a step in each grid game.",
           1, 12, 1),
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
           "strong players agree with it; never less than zero. Below 1, known beetles always pay more.", 0, 1),
        _t("GAME_POINTS_REFERENCE_CAP", "Matching proven experts or a trusted model", 0.6,
           "An answer that matches what proven experts (or a trusted model) say earns up to this share.", 0, 1),
        _t("GAME_RATER_MIN_JUDGED", "Answers on known beetles before a player can judge", 10,
           "Only players with at least this many answers on validated beetles, and a rating at or above the median, "
           "count as judges.", 1, 500, 1),
        _t("GAME_RATER_SPREAD", "How much stronger judges count", 0.1,
           "A judge counts by how their rating compares with yours (an Elo-like curve): smaller means stronger judges "
           "count far more than weaker ones.", 0.01, 1, 0.01),
        _t("GAME_REF_MODEL_MIN_CONFIDENCE", "Model: confidence to be a reference", 0.9,
           "The classifier's name counts as a reference only when it is at least this sure about the beetle...", 0, 1, 0.01),
        _t("GAME_REF_MODEL_MIN_PRECISION", "Model: proven accuracy in that taxon", 0.95,
           "...and has been right this often about that very taxon on validated beetles...", 0, 1, 0.01),
        _t("GAME_REF_MODEL_MIN_CHECKED", "Model: sure calls checked in that taxon", 20,
           "...over at least this many of its sure calls there.", 1, 1000, 1),
    ]),
    ("Naming confidence: what a beetle is, and is not", [
        _t("GAME_SELECT_TAP_WEIGHT", "A Select all tap, against a name", 0.8,
           "Tapping an unvalidated beetle in Select all counts as this share of a direct identification towards its "
           "name (down to the grid's rank). Taps never make an expert's verdict on their own.", 0, 1, 0.05),
        _t("GAME_TIP_MIN_VOTES", "Players needed for a name tip", 3,
           "Curators see 'N reliable players say genus X' once at least this many players agree...", 1, 50, 1),
        _t("GAME_TIP_MIN_SUPPORT", "Share of the weighted vote for a tip", 0.75,
           "...and their name has at least this share of the reliability-weighted vote at that rank. The same share "
           "applies to 'not in' tips.", 0.5, 1, 0.05),
        _t("GAME_TIP_MIN_NOT_VOTES", "Players needed for a 'not in' tip", 2,
           "Curators see 'Players are confident it is not in genus X' once at least this many say so.", 1, 50, 1),
        _t("GAME_AUTO_APPLY_MIN_EXPERTS", "Experts who must agree to write a name in", 2,
           "Proven experts who must give the same species before it is written onto an unnamed beetle as an "
           "Expert ID (still unvalidated, for a curator to confirm).", 1, 10, 1),
    ]),
    ("Experts (whose answers become trusted labels)", [
        _t("GAME_TRUST_MIN_ACCURACY", "Accuracy an expert needs", 0.9,
           "In a taxon, a player must be correct at least this often on validated beetles.", 0.5, 1, 0.01),
        _t("GAME_TRUST_IMAGES_PER_SPECIES", "Images that cover a species, genus or tribe", 5,
           "An expert must have answered this many validated images of each child they cover (all of them for one "
           "with fewer): the species of a genus, the genera of a tribe, the tribes of a subfamily.", 1, 100, 1),
        _t("GAME_TRUST_CHILDREN_SHARE", "Share of a taxon's children an expert must cover", 0.75,
           "Rounded up, so a taxon with three or fewer children (at 75%) needs all of them. A rare genus no "
           "longer stops anyone becoming a tribe expert.", 0.1, 1, 0.05),
        _t("GAME_TRUST_MIN_JUDGED", "Fewest answers in a taxon for an expert", 10,
           "Also the fewest validated images a taxon needs before anyone can be proven in it.", 1, 500, 1),
        _t("GAME_EXPERT_PERCENTILE", "Experts come from the top share of players", 0.25,
           "Only the most reliable players overall (this share, by rating) can be experts.", 0.01, 1, 0.01),
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
    return game_setting(key, TUNABLES[key]["default"])


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


def examples():
    """[(what, points)] for typical answers, so the effect of a change is easy to see."""
    v, rank, pair, w = numbers()
    total = sum(rank.values())
    part = v["GAME_POINTS_PARTICIPATION"]
    cap = v["GAME_POINTS_CONSENSUS_CAP"]
    upto_genus = rank["subfamily"] + rank["tribe"] + rank["genus"]
    size = {int(k): f for k, f in v["GAME_GRID_SIZE_FACTOR"].items()}
    odd_species = v["GAME_POINTS_ODD_WEIGHT"] * pair[2]
    select_species = v["GAME_POINTS_SELECT_WEIGHT"] * pair[2]
    rows = [
        ("Identification", "Species correct (every rank)", total * w),
        ("Identification", "Stopped at a correct genus", upto_genus * w),
        ("Identification", "Correct genus, wrong species", (upto_genus - v["GAME_POINTS_OVERREACH"] * rank["species"]) * w),
        ("Identification", "Correct subfamily only", rank["subfamily"] * w),
        ("Identification", "Wrong subfamily, claimed down to species", -total * v["GAME_POINTS_WRONG_FACTOR"] * w),
        ("Identification", "Species agreed by strong players (unvalidated beetle, most)", total * cap * w),
        ("Similarity", "Correct: same genus", pair[2]),
        ("Similarity", "Correct: different subfamilies", pair[-1]),
        ("Similarity", "'Same tribe' for two of one genus (cautious, true)", pair[1]),
        ("Similarity", "'Same genus' for two of one tribe (one rung too close)",
         pair[1] - v["GAME_POINTS_OVERREACH"] * pair[2]),
        ("Similarity", "'Different subfamilies' for two of one genus", -v["GAME_POINTS_PAIR_STEP"] * 3),
        ("Odd One Out", "Correct pick, species round, 4 beetles", odd_species * size[4]),
        ("Odd One Out", "Correct pick, species round, 16 beetles", odd_species * size[16]),
        ("Odd One Out", "Wrong pick, species round, 4 beetles", -odd_species * size[4] * v["GAME_POINTS_ODD_WRONG_FACTOR"]),
        ("Select all", "Perfect grid, species, 9 beetles", select_species * size[9]),
        ("Select all", "Perfect grid, species, 16 beetles", select_species * size[16]),
        ("Select all", "3 of 3 found plus one wrong tap, species, 9 beetles",
         select_species * size[9] / 3 * (3 - v["GAME_POINTS_SELECT_WRONG"])),
        ("Any game", "Skip (Identification, Similarity)", -v["GAME_POINTS_UNSURE"]),
        ("Any game", "Skip (Odd One Out, Select all)", v["GAME_POINTS_ODD_SKIP"]),
        ("Any game", "Taking part (added to every real answer)", part),
    ]
    return [(game, what, round(p, 2)) for game, what, p in rows]


def odd_guess(size):
    """
    What a blind Odd One Out pick in a grid of ``size`` is worth on average, as a share of a correct pick: correct 1 time
    in ``size``, wrong on every validated beetle of the rest, nothing on an AI beetle (scored by agreement, never below
    zero). The grid with the most AI beetles a player meets, so the fewest wrong picks, is the test.
    """
    from .game import odd_open_count
    from .game_levels import LEVELS

    ai = min(size - 2, max(2, odd_open_count(len(LEVELS), size)))
    return (1 - current("GAME_POINTS_ODD_WRONG_FACTOR") * (size - 1 - ai)) / size


def select_tap_all(size):
    """
    What tapping every beetle of a Select all grid of ``size`` is worth, as a share of a perfect grid: every validated
    member correct, every validated non-member wrong, AI beetles nothing. The grid with the most members for its
    non-members is the test.
    """
    from .game import SELECT_AI, SELECT_MEMBERS

    low, high = SELECT_MEMBERS[size]
    wrong = current("GAME_POINTS_SELECT_WRONG")
    return max((m - wrong * (size - m - a)) / m for m in range(low, high + 1)
               for a in range(SELECT_AI[size][1] + 1) if size - m - a >= m)


def checks():
    """[(rule, ok, why)]: does the current tuning still push players towards accurate answers?"""
    v, rank, pair, w = numbers()
    upto = {r: sum(rank[x] for x in RANKS[: i + 1]) for i, r in enumerate(RANKS)}
    overreach_ok = all(upto[RANKS[i - 1]] - v["GAME_POINTS_OVERREACH"] * rank[RANKS[i]] < upto[RANKS[i - 1]] + 1e-9
                       and v["GAME_POINTS_OVERREACH"] > 0 for i in range(1, 4))
    wrong_least = min(v["GAME_POINTS_OVERREACH"] * rank["tribe"] * w, v["GAME_POINTS_PAIR_STEP"],
                      v["GAME_POINTS_OVERREACH"] * pair[1])
    deeper = all(rank[RANKS[i]] >= rank[RANKS[i - 1]] for i in range(1, 4))
    sizes = [int(s) for s in GRID_SIZES]
    guesses, taps = {n: odd_guess(n) for n in sizes}, {n: select_tap_all(n) for n in sizes}
    factor = [v["GAME_GRID_SIZE_FACTOR"][s] for s in GRID_SIZES]
    per_size = lambda values: ", ".join(f"{n} beetles {e:+.2f}" for n, e in values.items())   # noqa: E731
    return [
        ("Stopping where you're sure beats guessing one rank further", overreach_ok,
         "Going one rank too far must earn less than stopping (the overreach cost is above 0)."),
        ("Deeper ranks are worth at least as much as the ones above", deeper,
         "Species ≥ genus ≥ tribe ≥ subfamily, so precise names pay most."),
        ("Skipping costs less than the smallest mistake", v["GAME_POINTS_UNSURE"] < wrong_least,
         f"A skip costs {v['GAME_POINTS_UNSURE']:g}; the smallest mistake costs about {wrong_least:.2f}."),
        ("A blind guess in Odd One Out loses on average", all(e < 0 for e in guesses.values()),
         f"Correct 1 time in 4, 9 or 16, and wrong on each validated beetle of the rest: expected {per_size(guesses)} "
         "× what a correct pick earns."),
        ("Tapping everything in Select all loses", all(e < 0 for e in taps.values()),
         f"Non-members are never fewer than members, so a wrong tap must cost more than a member earns: expected "
         f"{per_size(taps)} × a perfect grid."),
        ("Bigger grids are worth at least as much", factor == sorted(factor),
         "Points by grid size must not fall as the grids grow, or a player would do better to stay small."),
        ("IBBI-AI's sure calls are surer than its unsure ones", v["GAME_AI_SURE_FROM"] >= v["GAME_AI_UNSURE_BELOW"],
         "A grid's sure AI beetle and its unsure one come from bands that must not overlap."),
        ("Known beetles pay more than agreement on unknown ones",
         v["GAME_POINTS_CONSENSUS_CAP"] < 1 and v["GAME_POINTS_REFERENCE_CAP"] < 1,
         "Points on validated beetles are the real test of accuracy; agreement is capped below them."),
        ("Taking part earns less than a correct subfamily", v["GAME_POINTS_PARTICIPATION"] < rank["subfamily"] * w,
         "So the score follows accuracy, not just the number of answers."),
        ("A tap counts less than a name", v["GAME_SELECT_TAP_WEIGHT"] < 1,
         "Tapping a beetle among nine is a quicker, weaker judgement than naming it."),
        ("Experts must be very accurate", v["GAME_TRUST_MIN_ACCURACY"] >= 0.85,
         "Expert labels reach curators as trusted: below 85% that trust is not earned."),
    ]
