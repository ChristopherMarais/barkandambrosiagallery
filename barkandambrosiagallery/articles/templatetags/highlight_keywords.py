from django import template
from django.utils.safestring import mark_safe
import re

register = template.Library()

@register.filter(name='highlight_keywords')
def highlight_keywords(text, query):
    # Split the query into individual words
    words = query.split()
    # Escape special characters in each word for use in regular expression
    escaped_words = [re.escape(word) for word in words]
    # Use regular expression to find all occurrences of each word in text and wrap them with <span> tags
    highlighted_text = text
    for word in escaped_words:
        highlighted_text = re.sub(f'({word})', r'<span class="underline decoration-sky-600 decoration-2 font-semibold">\1</span>', highlighted_text, flags=re.IGNORECASE)
    return mark_safe(highlighted_text)

