import time
import logging
from Bio import Entrez
from colorama import Fore, Style
from articles.models import Article
from django.utils.dateparse import parse_date
from django.db import transaction, IntegrityError
from django.core.management.base import BaseCommand
# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

Entrez.email = "cleysinhonv@gmail.com"
class Command(BaseCommand):
    help = 'Fetch and save news data from API'

    def handle(self, *args, **options):
        def search_pubmed(query):
            handle = Entrez.esearch(db="pubmed", term=query, retmax=10000)  # Retorna até 10 resultados
            record = Entrez.read(handle)
            handle.close()
            return record["IdList"]

        def fetch_details(id_list):
            ids = ",".join(id_list)
            handle = Entrez.efetch(db="pubmed", id=ids, rettype="xml", retmode="text")
            records = Entrez.read(handle)
            handle.close()
            time.sleep(1.5)
            return records

        def save_articles_to_db(articles):
            for article in articles['PubmedArticle']:
                # Obter dados do artigo
                pmid = article['MedlineCitation']['PMID']
                pmcid = article.get('PubmedData', {}).get('ArticleIdList', [None])[0]  # Pode precisar de ajustes para pegar o correto
                doi = next((id for id in article.get('PubmedData', {}).get('ArticleIdList', []) if isinstance(id, str) and id.startswith("10.")), None)
                title = article['MedlineCitation']['Article']['ArticleTitle']
                abstract_text = " ".join(article['MedlineCitation']['Article'].get('Abstract', {}).get('AbstractText', [""]))
                journal_title = article['MedlineCitation']['Article']['Journal']['Title']
                journal_issn = article['MedlineCitation']['Article']['Journal'].get('ISSN', "")
                journal_issue = article['MedlineCitation']['Article']['Journal']['JournalIssue'].get('Issue', "")
                journal_volume = article['MedlineCitation']['Article']['Journal']['JournalIssue'].get('Volume', "")
                journal_year_of_publication = article['MedlineCitation']['Article']['Journal']['JournalIssue']['PubDate'].get('Year', None)
                language = article['MedlineCitation']['Article'].get('Language', [None])[0]
                pub_model = article['MedlineCitation']['Article'].get('Journal', {}).get('JournalIssue', {}).get('PubModel', '')
                publication_status = article.get('PubmedData', {}).get('PublicationStatus', None)

                # Listas de autores e afiliações
                author_full_name = ", ".join([
                    f"{author.get('ForeName', '')} {author.get('LastName', '')}".strip()
                    for author in article['MedlineCitation']['Article'].get('AuthorList', [])])                

                author_affiliation_details = "; ".join([
                    ", ".join([affiliation.get('Affiliation', '') for affiliation in author.get('AffiliationInfo', [])])
                    for author in article['MedlineCitation']['Article'].get('AuthorList', [])
                ])
                # Tipo de publicação e lista de subtipos (corrigido para lidar com lista)
                pub_type_list = ", ".join([pub_type for pub_type in article['MedlineCitation']['Article'].get('PublicationTypeList', [])])

                # **Correção: Grants list**
                grant_list_data = article['MedlineCitation']['Article'].get('GrantList', {})
                if isinstance(grant_list_data, dict):
                    grants = grant_list_data.get('Grant', [])
                else:
                    grants = []  # No grants available or wrong type

                grants_list = "; ".join([f"{grant.get('GrantID', '')} ({grant.get('Agency', '')})" for grant in grants])

                # **Correção: Lista de URLs de texto completo**
                article_id_list = article.get('PubmedData', {}).get('ArticleIdList', [])
                full_text_url_list = "; ".join([id_str for id_str in article_id_list if isinstance(id_str, str) and id_str.startswith("http")])

                # Atualizar ou criar o registro no banco de dados
                article_data={
                        'source': 'PubMed',
                        'pmid': pmid,
                        'pmcid': pmcid,
                        'doi': doi,
                        'title': title,
                        'abstract_text': abstract_text,
                        'journal_title': journal_title,
                        'journal_issn': journal_issn,
                        'journal_issue': journal_issue,
                        'journal_volume': journal_volume,
                        'journal_year_of_publication': int(journal_year_of_publication) if journal_year_of_publication else None,
                        'language': language,
                        'pub_model': pub_model,
                        'publication_status': publication_status,
                        'author_full_name': author_full_name,
                        'author_affiliation_details': author_affiliation_details,
                        'pub_type_list': pub_type_list,
                        'grants_list': grants_list,
                        'full_text_url_list': full_text_url_list,
                    }

                try:
                    art, created = Article.objects.update_or_create(
                                    id=article_data['doi'],
                                    defaults=article_data)
                    if created:
                        logger.info(f'{Fore.GREEN}New paper: {query} - {article_data["doi"]} - {article_data["title"]}{Style.RESET_ALL}')
                    else:
                        logger.info(f'{Fore.BLUE}Updated paper: {query} - {article_data["doi"]} - {article_data["title"]}{Style.RESET_ALL}')
                except:
                    logger.info(f'{article_data}')

        # Defina sua consulta de pesquisa
        query_ = ["Spacex","'Blue+Origin'","'Virgin+Galactic'",'Space-flights','Terraformation','Space+mission','Spacelab','Space+Shuttle','Micro-gravity','Space+Station','China+space+station','Tiangong+space+station','Bioregenerative+life+support+systems',
'Lunar+South+Pole','lunar+mare','lunar+regolith','lunar+highlands','Martian+Regolith','Cosmonaut','spaceship','parabolic+flight','space+flights','spacecraft','lunar+exploration',
'Mars+exploration','microgravity','International+space+station','space+biology','spaceflight','Moon+Base','mars+experiment','Astrobiology','Space+omics','Mars+exploration',
'Moon+exploration','exoplanet','biosignature','extraterrestrial+life','exobiology','james+webb+space+telescope','Hubble+telescope']
        #query_ = ["Spacex","'Blue+Origin'","'Virgin+Galactic'"]
        for query in query_:
            # Execute a pesquisa e recupere os IDs dos artigos
            for year in range(1950,2030,5):
                aux = f'( ({query}[Title]) OR ({query}[Title/Abstract]) ) AND ( ("{year}"[Date - Publication] : "{year + 5}"[Date - Publication]) )'
                print (aux)
                article_ids = search_pubmed(aux)

                # Recupere os detalhes dos artigos
                if article_ids:
                    articles = fetch_details(article_ids)
                    save_articles_to_db(articles)
