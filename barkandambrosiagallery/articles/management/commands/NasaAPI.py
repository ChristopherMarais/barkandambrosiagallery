import requests

# URL corrigida da API NTRS
url = "https://ntrs.nasa.gov/api/pubspace/search"

# Parâmetros da consulta
params = {
    "q": "Space biology",  # Substitua pelo seu termo de busca
    "fields": "abstract",  # Limitar os campos para apenas o abstract
    "page": 1,             # Página dos resultados (se precisar de paginação)
    "size": 1             # Número de resultados por página
    }

# Fazer a requisição GET
response = requests.get(url, params=params)

# Verificar o status da requisição
if response.status_code == 200:
    api_data = response.json()
    print (api_data)
    article_data = {
        'source': 'NASA API',  # Fonte fixa como 'NASA API'
        'pmid': None,  # Não disponível nos dados fornecidos pela NASA API
        'pmcid': None,  # Não disponível nos dados fornecidos pela NASA API
        'doi': None,  # Não disponível nos dados fornecidos pela NASA API
        'title': api_data.get('title'),
        'abstract_text': api_data.get('abstract'),
        'journal_title': api_data.get('meetings', [{}])[0].get('name') if api_data.get('meetings') else None,
        'journal_issn': None,  # Não disponível nos dados fornecidos pela NASA API
        'journal_issue': None,  # Não disponível nos dados fornecidos pela NASA API
        'journal_volume': None,  # Não disponível nos dados fornecidos pela NASA API
        'journal_year_of_publication': api_data.get('distributionDate')[:4] if api_data.get('distributionDate') else (
            api_data.get('submittedDate')[:4] if api_data.get('submittedDate') else None),
        'language': 'en',  # Assumindo padrão como 'en' (Inglês) se não especificado
        'pub_model': None,  # Não aplicável diretamente nos dados fornecidos pela NASA API
        'publication_status': api_data.get('status'),
        'author_full_name': '; '.join([aff['meta']['author']['name'] for aff in api_data.get('authorAffiliations', [])]),
        'author_affiliation_details': '; '.join([aff['meta']['organization']['name'] for aff in api_data.get('authorAffiliations', [])]),
        'pub_type_list': api_data.get('stiTypeDetails'),
        'grants_list': [funding.get('number') for funding in api_data.get('fundingNumbers', [])],
        'full_text_url_list': [download['links']['pdf'] for download in api_data.get('downloads', []) if 'links' in download and 'pdf' in download['links']],
    }
    print (article_data)    
else:
    print(f"Erro: {response.status_code} - {response.text}")
