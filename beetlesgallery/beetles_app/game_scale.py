"""
The site's one colour scale (#572): how good something is, worst to best. Grey is "not yet", then red, orange, yellow
and green, with a deeper green at the very top. Levels, the day streak, badges, accuracy, challenge and the expertise
tree all use it. Blue is kept for IBBI-AI and purple for the players' consensus: neither is ever on this scale.

The classes are in static/css/input.css: .scale-<step> (text), .scale-fill-<step> (bars, dots), .scale-chip-<step>
(a solid label), .scale-soft-<step> (a light label), .scale-card-<step> (a badge's border and tint) and .scale-glow
(the top level and the hardest badges). Levels have a light colour each, finer than the steps: .level-<n> (#606).
"""

STEPS = ("none", "fair", "decent", "good", "great", "excellent")
WORDS = {"none": "Not yet", "fair": "Fair", "decent": "Decent", "good": "Good", "great": "Great", "excellent": "Excellent"}
# The fill shades as colours, for drawing (the confetti): Tailwind gray-300, red-500, orange-500, yellow-400, green-500,
# green-700, the same as .scale-fill-<step>
HEX = {"none": "#d1d5db", "fair": "#ef4444", "decent": "#f97316", "good": "#facc15", "great": "#22c55e",
       "excellent": "#15803d"}

# A 0-1 value (accuracy, challenge): the upper bound of each step; from 0.85 up it is excellent
VALUE_STEPS = ((0.3, "fair"), (0.5, "decent"), (0.7, "good"), (0.85, "great"))


def value_step(value):
    """The step for a 0-1 value, e.g. 0.6 -> "good", 0.9 -> "excellent"; None (nothing to show yet) -> "none"."""
    if value is None:
        return "none"
    for top, step in VALUE_STEPS:
        if value < top:
            return step
    return "excellent"


# Levels 1-10 (#606): each its own light, hazy colour (.level-<n>), grey at 1, then red, red-orange, orange, amber,
# yellow, lime, light green and green; only the top level is filled, solid green, and glows. ``step`` is the level's
# place on the scale above, for whatever goes by steps; ``hex`` its colour for drawing (the level-up confetti).
LEVELS = {
    1: ("none", "#9ca3af"), 2: ("fair", "#ef4444"), 3: ("fair", "#f15a29"), 4: ("decent", "#f97316"),
    5: ("decent", "#f59e0b"), 6: ("good", "#facc15"), 7: ("good", "#84cc16"), 8: ("great", "#4ade80"),
    9: ("great", "#22c55e"), 10: ("excellent", "#15803d"),
}
TOP_LEVEL = max(LEVELS)


def _level(level):
    return max(1, min(TOP_LEVEL, int(level or 1)))


def level_step(level):
    """The step on the scale for a level, e.g. 5 -> "decent"; levels past the top take the top's."""
    return LEVELS[_level(level)][0]


def level_hex(level):
    """A level's own colour, e.g. 7 -> "#84cc16" (lime)."""
    return LEVELS[_level(level)][1]


def level_classes(level):
    """The classes of a level's badge, e.g. 2 -> "level-2", 10 -> "level-10 scale-glow" (the only filled one)."""
    n = _level(level)
    return f"level-{n}" + (" scale-glow" if n == TOP_LEVEL else "")


# The day streak: (fewest days, step), from the top down; under 3 days it is grey
STREAK_STEPS = ((100, "excellent"), (30, "great"), (14, "good"), (7, "decent"), (3, "fair"))


def streak_step(days):
    """The step for a day streak, e.g. 10 days -> "decent"."""
    return next((step for least, step in STREAK_STEPS if (days or 0) >= least), "none")
