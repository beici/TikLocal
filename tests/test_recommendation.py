"""Deep-selection behaviour: the interactive pool is bounded but pages stay complete."""
import json

from tiklocal.services.favorites import FavoriteService
from tiklocal.services.library import LibraryService
from tiklocal.services.recommendation import DIVERSITY_PICKS, POOL_CAP, RecommendService


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


def test_files_outside_the_sampled_pool_still_reach_the_feed(tmp_path):
    # The exploration pool is capped for cost, but the tail used to draw only from
    # that sample: a library bigger than the cap stopped at the sample size and the
    # rest of the files could never be shown, no matter how far the user scrolled.
    total = POOL_CAP + 80
    service, _ = _service(tmp_path, count=total)
    result = service.get_weighted_selection('video', limit=total, seed='beyond')
    assert len(result) == total
    assert len(set(result)) == total

def test_selection_cache_serves_a_deep_page_from_a_larger_bucket(monkeypatch):
    # Every page of a scroll rebuilt the same seeded order: page 10 re-picked 240
    # diversity slots plus the whole tail (~684ms on a 10k library) to slice 24
    # items. Buckets must answer from the cached longer order instead.
    from tiklocal.web import flow

    calls = []

    def select(*, file_type, limit, seed, source_id='', **kwargs):
        calls.append(limit)
        return [f'{file_type}{index}' for index in range(limit)]

    flow._SELECTION_CACHE.clear()
    first = flow._selection_for(select, 'video', 25, 'seed:v', '', {})
    assert calls == [32]
    deep = flow._selection_for(select, 'video', 300, 'seed:v', '', {})
    assert calls == [32, 512]
    assert deep[:len(first)] == first
    assert flow._selection_for(select, 'video', 40, 'seed:v', '', {}) == deep[:40]
    assert calls == [32, 512]
