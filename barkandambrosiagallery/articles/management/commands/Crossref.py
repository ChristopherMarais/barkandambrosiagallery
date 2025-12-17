import json
import time
import logging
from habanero import Crossref
from colorama import Fore, Style
from articles.models import Article
from django.db import transaction, IntegrityError
from django.core.management.base import BaseCommand


# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

class Command(BaseCommand):
    help = 'Busca artigos relacionados a "microgravity" e insere no banco de dados'

    def handle(self, *args, **kwargs):
        cr = Crossref()
        searchers = ['terraformation','Space mission','Spacelab','Space Shuttle','Micro-gravity','Space Station','China space station','Tiangong space station','Bioregenerative life+support systems','Lunar South Pole','lunar mare','lunar regolith','lunar highlands','Martian Regolith','Cosmonaut','spaceship','parabolic flight','space flights','spacecraft','lunar exploration','Mars exploration','microgravity','International space+station','space biology','spaceflight','Moon Base','mars experiment','Astrobiology','Space omics','Mars exploration','Moon exploration','exoplanet','biosignature','extraterrestrial life','exobiology','james webb space telescope','Hubble telescope']
        for query in searchers:
            results = cr.works(query=query, filter={'has-abstract':True}, limit=1000,offset=9000)
            for item in results['message']['items']:
                if item.get('title', [])[0].__contains__(query) or item.get('abstract', '').__contains__(query):
                    article_data = {
                                'id': item.get('DOI', ''),
                                'source': item.get('container-title', [''])[0],
                                'pmid': None,  # CrossRef API não retorna PMID
                                'pmcid': None,  # CrossRef API não retorna PMCID
                                'doi': item.get('DOI', ''),
                                'title': item.get('title', [])[0],
                                'author_full_name': ', '.join(
                                    [f"{author.get('given', '')} {author.get('family', '')}" for author in item.get('author', [])]
                                ),
                                'author_affiliation_details': ', '.join(
                                    [aff.get('name', '') for author in item.get('author', []) for aff in author.get('affiliation', [])]
                                ),
                                'journal_title': item.get('container-title', [''])[0],
                                'journal_issn': item.get('ISSN', [None])[0],
                                'journal_issue': item.get('issue', ''),
                                'journal_volume': item.get('volume', ''),
                                'journal_year_of_publication': item.get('published-print', {}).get('date-parts', [[None]])[0][0],
                                'abstract_text':item.get('abstract', ''),
                                'publication_status': None,  # CrossRef API não retorna status de publicação
                                'language': item.get('language', ''),
                                'pub_model': None,  # CrossRef API não retorna modelo de publicação
                                'pub_type_list': item.get('type', ''),
                                'grants_list': json.dumps(item.get('funder', [])),  # Serializando como JSON para armazenar em TextField
                                'full_text_url_list': ', '.join([link.get('URL', '') for link in item.get('link', [])]),
                            }
                            # Use update_or_create to avoid duplicate entries
                    try:
                        art, created = Article.objects.update_or_create(
                                    doi=article_data['doi'],
                                    defaults=article_data
                                )
                        if created:
                            logger.info(f'{Fore.GREEN}New paper: {query} - {article_data["doi"]} - {article_data["title"]}{Style.RESET_ALL}')
                        else:
                            logger.info(f'{Fore.BLUE}Updated existing paper: {query} - {article_data["doi"]} - {article_data["title"]}{Style.RESET_ALL}')
                    except:
                        logger.info(f'{article_data}')
            time.sleep(2)