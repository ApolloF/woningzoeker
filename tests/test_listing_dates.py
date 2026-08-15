from datetime import UTC, datetime

from bs4 import BeautifulSoup

from app.adapters.base import extract_published_at
from app.models import Listing
from app.schemas import NormalizedListing
from app.services.pipeline_base import Pipeline


def test_extracts_only_explicit_publication_timestamp() -> None:
    soup = BeautifulSoup(
        '<article><time datetime="2026-09-01">Beschikbaar</time>'
        '<time itemprop="datePosted" datetime="2026-08-15T18:05:00+02:00">Vandaag</time></article>',
        "html.parser",
    )
    article = soup.article
    assert article is not None
    assert extract_published_at(article) == datetime(2026, 8, 15, 16, 5, tzinfo=UTC)


def test_availability_time_is_not_mistaken_for_upload_time() -> None:
    soup = BeautifulSoup('<article><time datetime="2026-09-01">Beschikbaar</time></article>', "html.parser")
    article = soup.article
    assert article is not None
    assert extract_published_at(article) is None


def test_missing_date_on_later_poll_does_not_erase_known_publication_time() -> None:
    known = datetime(2026, 8, 15, 16, 5, tzinfo=UTC)
    stored = Listing(
        source_id=1,
        canonical_property_id=1,
        external_id="1",
        url="https://example.test/1",
        title="Teststraat 1",
        address="Teststraat 1",
        city="Groningen",
        published_at=known,
    )
    normalized = NormalizedListing(
        source_name="test",
        external_id="1",
        url="https://example.test/1",
        title="Teststraat 1",
        address="Teststraat 1",
        city="Groningen",
    )
    Pipeline._copy_normalized(stored, normalized)
    assert stored.published_at == known
