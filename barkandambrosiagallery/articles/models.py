from django.db import models

class Articles(models.Model):
    id = models.CharField(max_length=255, primary_key=True)
    source = models.CharField(max_length=250, null=True, blank=True)
    pmid = models.CharField(max_length=250, null=True, blank=True)
    pmcid = models.CharField(max_length=250, null=True, blank=True)
    doi = models.CharField(max_length=200, null=True, blank=True)
    title = models.TextField(null=True, blank=True)
    author_full_name = models.TextField(null=True, blank=True)  # Único campo para os autores
    author_affiliation_details = models.TextField(null=True, blank=True)  # Novo campo para afiliações
    journal_title = models.TextField(null=True, blank=True)
    journal_issn = models.CharField(max_length=250, null=True, blank=True)
    journal_issue = models.CharField(max_length=250, null=True, blank=True)
    journal_volume = models.CharField(max_length=250, null=True, blank=True)
    journal_year_of_publication = models.CharField(max_length=250, null=True, blank=True)
    abstract_text = models.TextField(null=True, blank=True)
    publication_status = models.CharField(max_length=250, null=True, blank=True)
    language = models.CharField(max_length=250, null=True, blank=True)
    pub_model = models.CharField(max_length=250, null=True, blank=True)
    pub_type_list = models.TextField(null=True, blank=True)
    grants_list = models.TextField(null=True, blank=True)
    full_text_url_list = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'space_literature'
