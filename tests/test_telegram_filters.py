from app.models import Decision, Listing
from app.schemas import Criteria
from app.services.telegram import TelegramNotifier


def listing(decision: Decision, score: int) -> Listing:
    return Listing(
        source_id=1,
        canonical_property_id=1,
        external_id="1",
        url="https://example.test/1",
        title="Teststraat 1",
        address="Teststraat 1",
        city="Groningen",
        decision=decision.value,
        match_score=score,
    )


def test_telegram_filter_supports_fit_score_combinations_and_off() -> None:
    review_high = listing(Decision.REVIEW, 90)
    auto_low = listing(Decision.AUTO_REACT, 70)
    assert TelegramNotifier._listing_matches_filter(
        review_high, Criteria(telegram_listing_filter="score", telegram_min_score=80)
    )
    assert TelegramNotifier._listing_matches_filter(
        auto_low, Criteria(telegram_listing_filter="auto_react", telegram_min_score=80)
    )
    assert TelegramNotifier._listing_matches_filter(
        auto_low, Criteria(telegram_listing_filter="auto_react_or_score", telegram_min_score=80)
    )
    assert not TelegramNotifier._listing_matches_filter(review_high, Criteria(telegram_listing_filter="off"))
