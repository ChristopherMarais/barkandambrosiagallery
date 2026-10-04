"""{% podium_popup %} on the game home: the top three of a week, month or year that just ended (game_podium.py)."""
from django import template

from beetlesgallery.beetles_app import game_podium

register = template.Library()


@register.inclusion_tag("beetles/includes/game_podium.html", takes_context=True)
def podium_popup(context):
    request = context.get("request")
    podiums = game_podium.unseen(request) if request is not None and request.user.is_authenticated else []
    return {"podiums": podiums, "request": request}
