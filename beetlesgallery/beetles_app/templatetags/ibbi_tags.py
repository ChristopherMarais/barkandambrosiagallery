"""{% ibbi_model_options %}: the <option>s of the AI model pickers (the classifier page, Classify with AI)."""
from django import template
from django.utils.html import format_html, format_html_join

from beetlesgallery.tools import ibbi_models

register = template.Library()


@register.simple_tag
def ibbi_model_options():
    return format_html_join(
        "", '<option value="{}"{}>{}</option>',
        ((key, format_html(" selected") if key == ibbi_models.DEFAULT else "", spec["label"])
         for key, spec in ibbi_models.MODELS.items()),
    )
