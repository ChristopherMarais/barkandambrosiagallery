import requests
from django.core.cache import cache

# gets data from api
def get_spaceflight_news():
    cached_data = cache.get('spaceflight_news')
    if cached_data:
        return cached_data
    else:
        url = "https://api.spaceflightnewsapi.net/v4/articles"
        response = requests.get(url)
        space_news = response.json()
        cache.set('spaceflight_news', space_news, timeout=(60 * 10))
        # print("Dados armazenados no cache:", space_news)  # Para fins de debug
        return space_news
