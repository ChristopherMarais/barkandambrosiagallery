from django import template

register = template.Library()

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
def rarity(value):
    """A 0-1 value's rarity tier ("common" ... "mythic"), for the .rarity-<tier> / .rarity-fill-<tier> classes."""
    from beetlesgallery.beetles_app.game_rewards import rarity_tier
    try:
        return rarity_tier(None if value is None else float(value))
    except (TypeError, ValueError):
        return "common"
