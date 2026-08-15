from app.adapters.base import BrowserSourceAdapter, SourceAdapter
from app.adapters.campus_groningen import CampusGroningenAdapter
from app.adapters.funda import FundaRentalAdapter
from app.adapters.gruno import GrunoVastgoedAdapter
from app.adapters.huurwoningen import HuurwoningenAdapter
from app.adapters.maxx import MaxxGroningenAdapter
from app.adapters.one_two_three_wonen import OneTwoThreeWonenAdapter
from app.adapters.pandomo import PandomoAdapter
from app.adapters.pararius import ParariusAdapter
from app.adapters.rotsvast import RotsvastGroningenAdapter
from app.adapters.woldring import WoldringAdapter

ALL_ADAPTERS: tuple[type[SourceAdapter], ...] = (
    OneTwoThreeWonenAdapter,
    WoldringAdapter,
    HuurwoningenAdapter,
    MaxxGroningenAdapter,
    GrunoVastgoedAdapter,
    RotsvastGroningenAdapter,
    PandomoAdapter,
    CampusGroningenAdapter,
    ParariusAdapter,
    FundaRentalAdapter,
)

__all__ = [
    "ALL_ADAPTERS",
    "BrowserSourceAdapter",
    "CampusGroningenAdapter",
    "FundaRentalAdapter",
    "GrunoVastgoedAdapter",
    "HuurwoningenAdapter",
    "MaxxGroningenAdapter",
    "OneTwoThreeWonenAdapter",
    "PandomoAdapter",
    "ParariusAdapter",
    "RotsvastGroningenAdapter",
    "SourceAdapter",
    "WoldringAdapter",
]
