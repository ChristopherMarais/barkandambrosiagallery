import re

from django import template
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe

register = template.Library()

# A URL (http/https), a bare "www." address, or a DOI written as "doi.org/..." / "dx.doi.org/..." (#618 detail-doi:
# notes often paste a protocols.io DOI without a scheme, e.g. "dx.doi.org/10.17504/protocols.io.xyz").
_LINKABLE_RE = re.compile(r"https?://[^\s<]+|www\.[^\s<]+|(?:dx\.)?doi\.org/[^\s<]+", re.IGNORECASE)


@register.filter
def linkify(text):
    """Plain text with any URL, "www." address or DOI turned into a clickable link; everything else is escaped, so
    this is safe to use directly on free-text notes (issue #618, detail-doi)."""
    if not text:
        return text
    text = str(text)
    pieces = []
    last = 0
    for m in _LINKABLE_RE.finditer(text):
        raw = m.group(0)
        href = raw if raw.lower().startswith("http") else f"https://{raw}"
        pieces.append(escape(text[last:m.start()]))
        pieces.append(f'<a href="{escape(href)}" class="underline" rel="noopener" target="_blank">{escape(raw)}</a>')
        last = m.end()
    pieces.append(escape(text[last:]))
    return mark_safe("".join(pieces))

# The longest side of a stored thumbnail (image_pipeline writes them at this size or smaller)
THUMB_SIDE = 96

@register.simple_tag
def thumb_size(asset):
    """width and height attributes for an <img> that shows asset.thumb_small.

    They tell the browser the picture's shape before it loads, so the page does not jump (layout shift). The sizes
    are the thumbnail's own (longest side THUMB_SIDE), so CSS can still size the image however it likes.
    """
    if not asset or not asset.image_width or not asset.image_height:
        return ""
    w, h = asset.image_width, asset.image_height
    scale = min(1, THUMB_SIDE / max(w, h))
    return format_html('width="{}" height="{}"', max(1, round(w * scale)), max(1, round(h * scale)))

@register.simple_tag(takes_context=True)
def remove_filter(context, field, value=None):
    """
    Returns a URL query string with the specified field removed.
    Also resets pagination to page 1 to avoid 'empty page' errors.
    """
    query = context['request'].GET.copy()
    
    # If a specific value is provided, remove only that value from the list
    if value and field in query:
        values = query.getlist(field)
        if value in values:
            values.remove(value)
            # If values remain, update the list; otherwise delete the key
            if values:
                query.setlist(field, values)
            else:
                del query[field]
    # If no value provided, remove the entire key (fallback)
    elif field in query:
        del query[field]

    # Reset pagination
    if 'page' in query:
        del query['page']
        
    return query.urlencode()

def _split_number(value, decimals):
    """(sign, groups of three digits, fraction text) for a number, or None for anything else."""
    from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip())
        decimals = max(int(decimals or 0), 0)
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not number.is_finite():
        return None
    text = f"{abs(number.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)):f}"
    whole, _, fraction = text.partition(".")
    groups = []
    while whole:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    sign = "-" if number < 0 and any(c not in "0." for c in text) else ""
    return sign, groups, f".{fraction}" if fraction else ""


@register.filter
def digit_groups(value, decimals=0):
    """
    Render a number with its digits in groups of three, each group a span with
    extra space before it (e.g. 70000 -> "70 000"), so large counts are easy to read.
    Rounded to ``decimals`` places (none by default): {{ score|digit_groups:1 }}.
    Non-numbers are returned unchanged. static/js/digit_groups.js does the same in the browser.
    """
    from django.utils.safestring import mark_safe

    parts = _split_number(value, decimals)
    if parts is None:
        return value
    sign, groups, fraction = parts
    gap = ' style="margin-left:0.4em"'
    spans = "".join(
        f'<span class="digit-group"{gap if i else ""}>{group}</span>'
        for i, group in enumerate(groups)
    )
    # Only digits and fixed markup go into the string, so it is safe to mark as such.
    return mark_safe(sign + spans + fraction)


@register.filter
def digit_groups_text(value, decimals=0):
    """digit_groups as plain text (a narrow space between groups), for places markup cannot go, like a title."""
    parts = _split_number(value, decimals)
    if parts is None:
        return value
    sign, groups, fraction = parts
    return sign + "\u202f".join(groups) + fraction


@register.simple_tag(takes_context=True)
def page_url(context, param, number, anchor=""):
    """This page's address with one page number changed (the rest of the query kept): {% page_url "labels_page" 2 "labels" %}."""
    query = context["request"].GET.copy()
    query[param] = number
    return "?" + query.urlencode() + (f"#{anchor}" if anchor else "")


@register.filter
def level_icon(level):
    """The game's icon for a level: {{ level|level_icon }} -> "fi-rr-worm"."""
    from beetlesgallery.beetles_app.game_levels import level_icon as icon

    return icon(level)


@register.simple_tag(takes_context=True)
def sort_link(context, param, key, label, anchor=""):
    """
    A table header that sorts by its column: click once for ascending, again for descending. Keeps the rest of the
    query, goes back to the first page of that table (``<table>_sort`` -> ``<table>_page``).
    """
    from django.utils.html import format_html

    query = context["request"].GET.copy()
    current = query.get(param) or context.get(param, "")
    active = current.lstrip("-") == key
    descending = current.startswith("-")
    query[param] = f"-{key}" if active and not descending else key
    query.pop(param.replace("_sort", "_page"), None)
    arrow = (" \u2193" if descending else " \u2191") if active else ""
    return format_html('<a href="?{}{}" class="hover:text-gray-900 {}" data-sort="{}">{}{}</a>', query.urlencode(),
                       f"#{anchor}" if anchor else "", "text-gray-900" if active else "", key, label, arrow)


@register.simple_tag
def taxon_url(subfamily="", tribe="", genus=""):
    """
    A link into the taxonomy browser that opens the tree only as deep as the taxon named (issue #419): pass the
    subfamily for a subfamily link, subfamily and tribe for a tribe, and all three for a genus. A species links by
    its id instead (?species=...).
    """
    from urllib.parse import urlencode

    from django.urls import reverse

    params = [(k, (v or "").strip()) for k, v in (("subfamily", subfamily), ("tribe", tribe), ("genus", genus))]
    return reverse("taxonomy_browser") + "?" + urlencode([(k, v) for k, v in params if v])


@register.filter
def percent(value):
    """A fraction (0-1) as a CSS percentage number, e.g. 0.25 -> "25.000" (for box positions)."""
    try:
        return "%.3f" % (float(value) * 100)
    except (TypeError, ValueError):
        return "0"


@register.filter
def game_label(key):
    """A game's name as players see it ("pair" -> "Similarity"), from game_levels.GAME_NAMES."""
    from beetlesgallery.beetles_app.game_levels import GAME_NAMES
    return GAME_NAMES.get(key, key)


@register.filter
def scale(value):
    """A 0-1 value's step on the site's scale ("none", "fair" ... "excellent"), for .scale-<step> / .scale-fill-<step>."""
    from beetlesgallery.beetles_app.game_scale import value_step
    try:
        return value_step(None if value is None else float(value))
    except (TypeError, ValueError):
        return "none"


@register.filter
def level_scale(level):
    """The classes of a level's badge, its own colour (#606): {{ 10|level_scale }} -> "level-10 scale-glow"."""
    from beetlesgallery.beetles_app.game_scale import level_classes
    return level_classes(level)


@register.filter
def streak_scale(days):
    """A day streak's step on the scale: {{ 10|streak_scale }} -> "decent"."""
    from beetlesgallery.beetles_app.game_scale import streak_step
    return streak_step(days)


@register.filter
def dash(value):
    """
    The site's one "no value" mark (site-dash, #618): an em dash for a cell that must show something, rather than
    an en dash, a hyphen or "No ID". Prefer leaving the field out entirely where that is possible instead of
    reaching for this filter. {{ obj.note|dash }} -> "—" when note is empty, else the note unchanged.
    """
    if value is None or value == "" or value == "None":
        return "—"
    return value


# The site's sections and their nav prefix, home excepted (it is "/", which would match everything): shared by
# nav_active (which nav item is current) and nav_section_title (the mobile top bar's section name), so a sub-page
# added under one of these prefixes is picked up by both without template changes (nav-active, nav-mobile-title,
# #618).
def _nav_sections(context):
    """Not a template tag: a plain helper for nav_section_title below."""
    game_name = context.get("game_name") or "Bark & Ambrosia Detective"
    return (
        ("/beetles/", "Image Browser"),
        ("/taxonomy/", "Taxonomy Browser"),
        ("/interactions/", "Interactions"),
        ("/tools/classify/", "AI Identification"),
        ("/tools/annotate/", "Image Annotation"),
        ("/game/", game_name),
        ("/my-uploads/", "Data Management"),
        ("/accounts/me/", "Account & settings"),
        ("/accounts/login/", "Login"),
    )


# Pages that live outside their section's own URL prefix but sit below it in the menu (nav-active, #618): the nav
# item's prefix -> the other prefixes that mark it current too. Account and Login are never shown together (one for
# signed-in visitors, one for the rest), so the sign-up pages can sit under both.
NAV_EXTRA_PREFIXES = {
    "/my-uploads/": ("/tools/predictions/", "/tools/bulk-validate/", "/upload/", "/updates/"),
    "/accounts/me/": ("/tools/site-notice/", "/tools/access-requests/", "/accounts/create-account/",
                      "/accounts/signup/", "/accounts/request-access/"),
    "/accounts/login/": ("/accounts/signup/", "/accounts/request-access/", "/accounts/password-reset/",
                         "/accounts/set-password/", "/accounts/verify-email/"),
}
# Pages below Home, which can't use a prefix of its own ("/" would match everything).
NAV_HOME_PREFIXES = ("/team/",)


def _in_section(path, prefix):
    return path.startswith(prefix) or any(path.startswith(p) for p in NAV_EXTRA_PREFIXES.get(prefix, ()))


@register.simple_tag(takes_context=True)
def nav_active(context, prefix):
    """
    The classes for a nav item, current or not (nav-active, #618): matched by URL prefix (e.g. "/game/"), not an
    exact page name, so every sub-page of a section (Leaderboard, Unlocks, ... under "/game/") marks its parent nav
    item too, as do the pages listed for it in NAV_EXTRA_PREFIXES (Predictions under Data Management, ...). Not for
    the home link, whose own prefix ("/") would match every page: see nav_home_active.
    """
    request = context.get("request")
    path = getattr(request, "path", "") or ""
    return "bg-gray-200 font-semibold" if _in_section(path, prefix) else "hover:bg-gray-200"


@register.simple_tag(takes_context=True)
def nav_home_active(context):
    """The home link's classes: current on the home page itself and on the pages below it (NAV_HOME_PREFIXES)."""
    request = context.get("request")
    path = getattr(request, "path", "") or ""
    if path == "/" or any(path.startswith(p) for p in NAV_HOME_PREFIXES):
        return "bg-gray-200 font-semibold"
    return "hover:bg-gray-200"


@register.simple_tag(takes_context=True)
def nav_section_title(context):
    """The current section's short name for the mobile top bar, next to the menu button (nav-mobile-title, #618)."""
    request = context.get("request")
    path = getattr(request, "path", "") or ""
    if path == "/":
        return "Bark and Ambrosia Gallery"
    user = context.get("user") or getattr(request, "user", None)
    signed_in = bool(getattr(user, "is_authenticated", False))
    for prefix, label in _nav_sections(context):
        # the same rule as the menu: Account only for signed-in visitors, Login only for the rest
        if prefix == ("/accounts/login/" if signed_in else "/accounts/me/") and not path.startswith(prefix):
            continue
        if _in_section(path, prefix):
            return label
    return "Bark and Ambrosia Gallery"
