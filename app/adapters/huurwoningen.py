from app.adapters.base import BrowserSourceAdapter
from app.adapters.pararius_family import ParariusFamilyParser


class HuurwoningenAdapter(ParariusFamilyParser, BrowserSourceAdapter):
    source_name = "huurwoningen"
    display_name = "Huurwoningen.nl"
    search_url = "https://www.huurwoningen.nl/in/groningen/"
    ready_selector = ".listing-search-item__content"
