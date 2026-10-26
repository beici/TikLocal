"""Deep-selection behaviour: the interactive pool is bounded but pages stay complete."""
import json

from tiklocal.services.favorites import FavoriteService
from tiklocal.services.library import LibraryService
from tiklocal.services.recommendation import DIVERSITY_PICKS, RecommendService


def _service(tmp_path, count=40):
    for index in range(count):
        (tmp_path / f'clip{index:02d}.mp4').write_bytes(b'video')
    library = LibraryService(tmp_path)
    favorites = FavoriteService(tmp_path / 'favorites.json', library)
    return RecommendService(library, favorites), library


def test_a_deep_page_still_returns_the_full_requested_slice(tmp_path):
    service, library = _service(tmp_path, count=DIVERSITY_PICKS + 40)
    total = len(library.scan_videos())
    assert total > DIVERSITY_PICKS

    first = service.get_weighted_selection('video', limit=total, seed='deep')
    assert len(first) == total
    assert len(set(first)) == total

    # Same seed must reproduce the page, including the weighted tail, so a reload
    # does not reshuffle already rendered items.
    second = service.get_weighted_selection('video', limit=total, seed='deep')
    assert second == first


def test_the_interactive_prefix_is_unchanged_by_a_larger_page(tmp_path):
    service, _ = _service(tmp_path)
    small = service.get_weighted_selection('video', limit=4, seed='prefix')
    large = service.get_weighted_selection('video', limit=30, seed='prefix')
    assert large[: len(small)] == small


def test_cold_start_feed_degrades_to_the_weighted_tail(tmp_path):
    # A brand new profile has no impressions at all; the exploration branch used to
    # consume the pool one item at a time until it emptied.
    service, _ = _service(tmp_path, count=12)
    result = service.get_weighted_selection('video', limit=12, seed='cold')
    assert len(result) == 12
    assert json.dumps(result)  # plain strings, serialisable for the feed payload
