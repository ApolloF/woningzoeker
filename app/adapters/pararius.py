from app.adapters.base import BrowserSourceAdapter
from app.adapters.pararius_family import ParariusFamilyParser


class ParariusAdapter(ParariusFamilyParser, BrowserSourceAdapter):
    source_name = "pararius"
    display_name = "Pararius"
    search_url = "https://www.pararius.nl/huurwoningen/groningen"
    ready_selector = ".listing-search-item__content"
