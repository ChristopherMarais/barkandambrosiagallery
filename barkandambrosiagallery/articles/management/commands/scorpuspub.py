import requests

# Sua chave de API
api_key = '8b3fb5ca5f287a4e2e62e22a71c07de5'

# URL da API do Scopus
url = 'https://api.elsevier.com/content/search/scopus'

# Parâmetros da consulta
params = {
    'query': 'space biology',
    'field': (
        'dc:identifier',                 # Equivalente ao 'pmid' ou 'pmcid' (use para armazenar o SCOPUS ID)
        'dc:title',                      # Equivalente ao 'title'
        'dc:creator',                    # Equivalente ao 'author_full_name'
        'affiliation',                   # Equivalente ao 'author_affiliation_details'
        'prism:publicationName',         # Equivalente ao 'journal_title'
        'prism:issn',                    # Equivalente ao 'journal_issn'
        'prism:issueIdentifier',         # Equivalente ao 'journal_issue'
        'prism:volume',                  # Equivalente ao 'journal_volume'
        'prism:coverDate',               # Equivalente ao 'journal_year_of_publication'
        'dc:description',                # Equivalente ao 'abstract_text'
        'subtype',                       # Equivalente ao 'publication_status' (pode precisar de mapeamento adicional)
        'prism:doi',                     # Equivalente ao 'doi'
        'prism:aggregationType',         # Pode ser usado para determinar 'pub_model'
        'subtypeDescription',            # Pode ser usado para determinar 'pub_type_list'
        'prism:url',                     # Equivalente ao 'full_text_url_list'
        'language',                      # Equivalente ao 'language'
        'fund-acr',                      # Equivalente ao 'grants_list' (se disponível)
    )
}

# Cabeçalhos incluindo a chave de API
headers = {
    'X-ELS-APIKey': api_key
}

# Fazendo a requisição GET
response = requests.get(url, headers=headers, params=params)

# Verificando o status da resposta
if response.status_code == 200:
    data = response.json()
    print(data)  # Exibir os dados retornados
else:
    print(f"Erro: {response.status_code} - {response.text}")
