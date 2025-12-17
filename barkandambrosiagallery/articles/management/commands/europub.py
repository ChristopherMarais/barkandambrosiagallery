import requests
import logging
import time, sys
from datetime import datetime
from colorama import Fore, Style
from articles.models import Article
from django.core.management.base import BaseCommand

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

class Command(BaseCommand):
    help = 'Fetch and save news data from API'

    def handle(self, *args, **options):
        def parse_article_data(json_data):
            articles = []
            nextPageUrl = json_data.get('nextPageUrl', {})
            for result in json_data.get('resultList', {}).get('result', []):
                # Concatenar todos os nomes completos dos autores
                author_names = [author.get('fullName') for author in result.get('authorList', {}).get('author', []) if author.get('fullName')]

                author_full_name = "; ".join(author_names)  # Concatenar com ponto e vírgula

                affiliation_names = []
                for author in result.get('authorList', {}).get('author', []):
                    # Extrai a afiliação de cada autor
                    affiliations = author.get('authorAffiliationDetailsList', {}).get('authorAffiliation', [])
                    for affiliation in affiliations:
                        affiliation_names.append(affiliation.get('affiliation'))
                author_affiliation_details = "; ".join(affiliation_names)

                article = Article(
                    id=result.get('id'),
                    source=result.get('source'),
                    pmid=result.get('pmid'),
                    pmcid=result.get('pmcid'),
                    doi=result.get('doi'),
                    title=result.get('title'),
                    author_full_name=author_full_name,
                    author_affiliation_details = author_affiliation_details,
                    journal_title=result.get('journalInfo', {}).get('journal', {}).get('title'),
                    journal_issn=result.get('journalInfo', {}).get('journal', {}).get('issn'),
                    journal_issue=result.get('journalInfo', {}).get('issue'),
                    journal_volume=result.get('journalInfo', {}).get('volume'),
                    journal_year_of_publication=result.get('journalInfo', {}).get('yearOfPublication'),
                    abstract_text=result.get('abstractText'),
                    publication_status=result.get('publicationStatus'),
                    language=result.get('language'),
                    pub_model=result.get('pubModel'),
                    pub_type_list=", ".join(result.get('pubTypeList', {}).get('pubType', [])),
                    grants_list=", ".join([grant.get('agency') for grant in result.get('grantsList', {}).get('grant', [])]),
                    full_text_url_list=", ".join([url.get('url') for url in result.get('fullTextUrlList', {}).get('fullTextUrl', [])]),
                )
                articles.append(article)
            return articles,nextPageUrl

        def fetch_pubmed_data(query, base_url, result_type='core', format_type='json'):
            if len(base_url) < 1:
                base_url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
                params = {
                    'query': query,
                    'resultType': result_type,
                    'format': format_type,
                    'limit':999
                }

                response = requests.get(base_url, params=params)
                
                if response.status_code == 200:
                    return response.json()
                else:
                    raise Exception(f"Failed to fetch data: {response.status_code}")
            else:
                time.sleep(5)
                response = requests.get(base_url)
                
                if response.status_code == 200:
                    return response.json()
                else:
                    raise Exception(f"Failed to fetch data: {response.status_code}")
        
        def save_to_db(articles,search):
            for article in articles:
                try:
                    art, created = Article.objects.update_or_create(
                        id=article.doi,  # Chave primária usada para encontrar o registro existente
                        defaults={
                            'source': article.source,
                            'pmid': article.pmid,
                            'pmcid': article.pmcid,
                            'doi': article.doi,
                            'title': article.title,
                            'author_full_name': article.author_full_name,
                            'author_affiliation_details': article.author_affiliation_details,
                            'journal_title': article.journal_title,
                            'journal_issn': article.journal_issn,
                            'journal_issue': article.journal_issue,
                            'journal_volume': article.journal_volume,
                            'journal_year_of_publication': article.journal_year_of_publication,
                            'abstract_text': article.abstract_text,
                            'publication_status': article.publication_status,
                            'language': article.language,
                            'pub_model': article.pub_model,
                            'pub_type_list': article.pub_type_list,
                            'grants_list': article.grants_list,
                            'full_text_url_list': article.full_text_url_list,
                        }
                    )
                    if created:
                        logger.info( f"{Fore.GREEN}New paper: {search_query} - {article.doi} - {article.title} {Style.RESET_ALL}" )
                    else:
                        logger.info(f'{Fore.BLUE}Updated paper: {search_query} - {article.doi} - {article.title}{Style.RESET_ALL}')
                except:
                    logger.info(f'{article}')

        searchers = ["Spacex","'Blue+Origin'","'Virgin+Galactic'",'Space mission','Spacelab','Space Shuttle','Micro-gravity','China space station','Tiangong space station','Bioregenerative life support systems','Lunar South Pole','lunar mare','lunar regolith','lunar highlands','Martian Regolith','Cosmonaut','spaceship','parabolic flight','space flights','spacecraft','plant diseases in space','lunar exploration','Mars exploration','microgravity','International space station','space biology','spaceflight','Moon Base','mars experiment','Astrobiology','Space omics','Mars exploration','Moon exploration','exoplanet','biosignature','extraterrestrial life','exobiology','james webb space telescope','Hubble telescope']
        for search in searchers:
            for year in range(1950,2030,5):
                search_query = f'( (FIRST_PDATE:[{year}-01-01 TO {year + 5}-12-31]) AND (ABSTRACT:"{search}" OR TITLE:"{search}") AND (((SRC:AGR OR SRC:CBA OR SRC:CTX OR SRC:ETH OR SRC:HIR OR SRC:MED OR SRC:NBK OR SRC:PAT OR SRC:PMC OR SRC:PRR)) OR PUB_TYPE:REVIEW OR SRC:PPR) )'

                base_url = ''
                data = fetch_pubmed_data(search_query,base_url)
                articles, nextUrl = parse_article_data(data)
                save_to_db(articles,search)
                while nextUrl != '':
                    data = fetch_pubmed_data(search_query, nextUrl)
                    articles, nextUrl = parse_article_data(data)
                    save_to_db(articles,search_query)
                    if not nextUrl:
                        break
                    print (nextUrl)
                    time.sleep(1)
                time.sleep(1)