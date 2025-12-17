from django import template
from django.utils.dateparse import parse_datetime
from datetime import datetime

register = template.Library()

@register.filter
def format_published_at(value):
    # Convertendo a string de data/hora para um objeto datetime
    published_datetime = datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ')
    # Formatação da data conforme o formato desejado
    formatted_date = published_datetime.strftime('%Y/%m/%d %H:%M')  # Formato: '25 Oct 2006'
    return formatted_date