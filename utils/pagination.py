from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    """50 par page par défaut ; le client peut demander jusqu'à 5000 (?page_size=)."""
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 5000
