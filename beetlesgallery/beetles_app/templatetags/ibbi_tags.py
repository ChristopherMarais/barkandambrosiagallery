"""{% ibbi_model_options %}: the <option>s of the AI model pickers (the AI page, Generate AI recommendation)."""
from django import template
from django.utils.html import format_html, format_html_join

from beetlesgallery.tools import ibbi_models

register = template.Library()


@register.simple_tag
def ibbi_model_options():
    """Each model by its name; the default comes first, selected and marked as recommended."""
    return format_html_join(
        "", '<option value="{}"{}>{}</option>',
        ((key, format_html(" selected") if key == ibbi_models.DEFAULT else "",
          f"{spec['label']} (recommended)" if key == ibbi_models.DEFAULT else spec["label"])
         for key, spec in ibbi_models.MODELS.items()),
    )
