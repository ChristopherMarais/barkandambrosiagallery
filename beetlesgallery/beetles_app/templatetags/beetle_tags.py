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