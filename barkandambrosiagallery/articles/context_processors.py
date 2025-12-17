from .models import Articles

def recent_articles(request):
    recent_articles = Articles.objects.filter(doi__contains='/', journal_year_of_publication__gte=2024)[:5]
    
    return {'recent_articles': recent_articles}
