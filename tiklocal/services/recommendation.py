import datetime
import math
import random
import time
import zlib

from tiklocal.services.library import LibraryService, VIDEO_EXTENSIONS
from tiklocal.services.favorites import FavoriteService

# Media this old, and not shown for as long, counts as forgotten.
FORGOTTEN_DAYS = 180

# Building the candidate list reads every index row (0.2s on 8000 files) and the
# favourite test normalises each URI; a short cache keeps a page burst cheap while
# a newly imported file still shows up within a couple of seconds.
CANDIDATE_TTL_SEC = 2.0
# Two file types across MAX_SOURCES (24) plus a little headroom for a source that was
# just removed but is still referenced by an in-flight request.
MAX_CANDIDATE_CACHE = 64

# Beyond this many picks the O(picks * pool) diversity reshuffle stops paying off; the
# remaining items follow one seed-stable weighted ordering instead.
DIVERSITY_PICKS = 240

# The weighted pick rescans the whole pool on every pick. Capping the pool keeps a
# feed page's cost flat as the library grows; sampling is seeded, so pages stay stable.
POOL_CAP = 1500


def _parse_shown_at(value: object) -> datetime.datetime | None:
    """Parse a stored timestamp into naive local time.

    Impressions are written as UTC ISO strings; callers compare them against
    ``datetime.now()`` (naive local), so the offset must be applied here instead
    of being dropped, otherwise the comparison shifts by the UTC offset.
    """
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def shown_within(profile: dict | None, days: float, now: datetime.datetime) -> bool:
    shown = _parse_shown_at((profile or {}).get('last_shown_at'))
    return bool(shown and (now - shown).total_seconds() < days * 86400)


class RecommendService:
    def __init__(
        self,
        library_service: LibraryService,
        favorite_service: FavoriteService,
        activity_store=None,
        media_index=None,
    ):
        self.library = library_service
        self.favorites = favorite_service
        self.activity_store = activity_store
        self.media_index = media_index
        self._candidate_cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}

    def _candidates(self, file_type: str, source_id: str) -> list[dict]:
        if self.media_index:
            return [
                {'uri': item['name'], 'mtime': item['mtime_ts']}
                for item in self.media_index.records(
                    media_type=file_type, source_id=source_id, album_covers_only=True,
                )
            ]
        paths = self.library.scan_videos() if file_type == 'video' else self.library.scan_images()
        return [
            {'uri': uri, 'mtime': path.stat().st_mtime}
            for path in paths
            if (uri := self.library.get_relative_path(path))
        ]

    def _cached_candidates(self, file_type: str, source_id: str) -> list[dict]:
        key = (file_type, source_id)
        cached = self._candidate_cache.get(key)
        now = time.monotonic()
        if cached and now - cached[0] < CANDIDATE_TTL_SEC:
            return cached[1]
        candidates = self._candidates(file_type, source_id)
        self._candidate_cache[key] = (now, candidates)
        # A removed source used to keep its whole candidate list for the life of the
        # process, and re-adding the same folder under a new id leaked another copy.
        if len(self._candidate_cache) > MAX_CANDIDATE_CACHE:
            live = {source.id for source in getattr(self.library, 'sources', None) or []}
            for stale_key in [item for item in self._candidate_cache if item[1] not in live]:
                self._candidate_cache.pop(stale_key, None)
            while len(self._candidate_cache) > MAX_CANDIDATE_CACHE:
                self._candidate_cache.pop(next(iter(self._candidate_cache)))
        return candidates

    def get_weighted_selection(self, file_type='video', limit=20, seed=None, source_id='',
                               session_start=None) -> list[str]:
        """Get intelligent random selection of files.

        ``session_start`` (datetime) marks when the current feed session began: an
        item shown after that moment is not penalised again, otherwise every page
        request would re-rank the pool and page 2 repeated cards from page 1.
        """
        candidates = self._cached_candidates(file_type, source_id)
        if not candidates:
            return []

        # read_only() reuses the parse until favorites.json changes; load() would
        # parse the whole file here and again in reasons_for() for one page.
        favs = self.favorites.read_only()
        names = [item['uri'] for item in candidates]
        profiles = self.activity_store.profiles_for(names) if self.activity_store else {}
        dimension_scores = self.activity_store.dimension_scores() if self.activity_store else {}
        now = datetime.datetime.now()
        weighted_pool: list[dict] = []

        for candidate in candidates:
            try:
                rel_path = candidate['uri']
                mtime = candidate['mtime']
                is_fav = rel_path in favs
                base_score = 2.0 if is_fav else 1.0
                age_days = max((now - datetime.datetime.fromtimestamp(mtime)).total_seconds() / 86400, 0.0)
                time_score = math.exp(-age_days / 180.0)
                profile = profiles.get(rel_path) or {}
                affinity = max(-1.0, min(float(profile.get('affinity_score') or 0.0), 1.8))
                revisit_score = max(0.65, 1.0 + affinity * 0.2)
                recent_penalty = self._recent_view_weight(profile.get('last_shown_at'), now, session_start)
                dimensions = self.activity_store.dimensions_for(rel_path, file_type) if self.activity_store else []
                preference_score = sum(dimension_scores.get(dimension, 0.0) for dimension in dimensions)
                preference_weight = max(0.8, min(1.25, 1.0 + preference_score * 0.08))
                weighted_pool.append({
                    'uri': rel_path,
                    'weight': base_score * (0.25 + time_score) * revisit_score * recent_penalty * preference_weight,
                    'impressions': int(profile.get('impressions') or 0),
                    'dimensions': dict(dimensions),
                })
            except Exception:
                continue

        rng = random.Random(seed) if seed else random
        result: list[str] = []
        recent_dimensions: list[dict[str, str]] = []
        pool = weighted_pool[:]
        # Every pick rescans the pool, so the cost is O(picks * pool). Sampling the
        # pool down keeps a page of 24 fast on an 8000-file library (~168k weight
        # calls before) without changing the distribution: weights are preserved by
        # the weighted pick below, and the tail order stays seed-stable.
        if len(pool) > POOL_CAP:
            pool = rng.sample(pool, POOL_CAP)
        # The interactive reshuffle below is O(picks * pool): a deep feed page asking for
        # thousands of items froze the worker for seconds. Keep full fidelity for normal
        # page sizes and let the tail follow a seed-stable weighted order, so successive
        # pages of the same seed still line up.
        picks_target = min(limit, len(pool), DIVERSITY_PICKS)
        while pool and len(result) < picks_target:
            if len(result) % 4 == 3:
                if session_start is not None:
                    # Ordering the exploration pool by impression count reshuffled it on
                    # every request (each page records impressions), so page 2 repeated
                    # page 1's cards. A seed-stable hash keeps the intent per session.
                    least_seen = sorted(pool, key=lambda item: zlib.crc32(
                        f"{seed}:{item['uri']}".encode('utf-8')))
                else:
                    least_seen = sorted(pool, key=lambda item: item['impressions'])
                exploration_pool = least_seen[:max(1, len(least_seen) // 3)]
                varied = [item for item in exploration_pool if self._diversity_weight(item, recent_dimensions) >= 1.0]
                if varied:
                    exploration_pool = varied
                chosen = rng.choice(exploration_pool)
            else:
                weighted = [
                    (item, item['weight'] * self._diversity_weight(item, recent_dimensions))
                    for item in pool
                ]
                total_weight = sum(weight for _, weight in weighted)
                if total_weight <= 0:
                    break
                pick = rng.random() * total_weight
                current = 0.0
                chosen = weighted[-1][0]
                for item, weight in weighted:
                    current += weight
                    if current >= pick:
                        chosen = item
                        break

            result.append(chosen['uri'])
            recent_dimensions = (recent_dimensions + [chosen['dimensions']])[-3:]
            pool.remove(chosen)

        if len(result) < min(limit, len(weighted_pool)):
            # The tail has to draw from every candidate, not just the sampled pool:
            # with 1500 sampled out of 3000+ items the sample ran out first, so
            # has_more went false while thousands of files had never been shown.
            tail_rng = random.Random(f"{seed}:tail") if seed else random
            chosen_uris = set(result)
            keyed = [
                (tail_rng.random() ** (1.0 / max(item['weight'], 1e-9)), item)
                for item in weighted_pool
                if item['uri'] not in chosen_uris
            ]
            keyed.sort(key=lambda pair: pair[0], reverse=True)
            for _, item in keyed:
                if len(result) >= min(limit, len(weighted_pool)):
                    break
                result.append(item['uri'])
        return result

    def get_forgotten_selection(self, file_type='video', limit=20, seed=None, source_id='',
                                session_start=None) -> list[str]:
        """Old media not shown for a long time; what was once finished, replayed or favorited comes first."""
        if not self.media_index:
            return []
        now = datetime.datetime.now()
        cutoff = now.timestamp() - FORGOTTEN_DAYS * 86400
        names = [
            item['name'] for item in self.media_index.records(
                media_type=file_type, source_id=source_id, album_covers_only=True,
            )
            if 0 < float(item.get('mtime_ts') or 0) <= cutoff
        ]
        profiles = self.activity_store.profiles_for(names) if self.activity_store else {}
        favs = self.favorites.read_only()
        rng = random.Random(seed) if seed else random
        keyed = []
        for name in names:
            profile = profiles.get(name) or {}
            if shown_within(profile, FORGOTTEN_DAYS, now):
                continue
            loved = profile.get('completes') or profile.get('replays') or self.library.is_uri_in_set(name, favs)
            # Weighted shuffle: a larger weight pulls the random key toward 1.
            keyed.append((rng.random() ** (1 / (3.0 if loved else 1.0)), name))
        keyed.sort(reverse=True)
        return [name for _, name in keyed[:limit]]

    @staticmethod
    def _diversity_weight(candidate: dict, recent: list[dict[str, str]]) -> float:
        dimensions = candidate.get('dimensions') or {}
        directory = dimensions.get('directory')
        source = dimensions.get('source')
        directory_count = sum(1 for item in recent if directory and item.get('directory') == directory)
        source_count = sum(1 for item in recent if source and item.get('source') == source)
        weight = 0.35 if directory_count >= 2 else 1.0
        if source_count >= 3:
            weight *= 0.5
        return weight

    def reasons_for(self, uris: list[str]) -> dict[str, str]:
        """Return a short, user-facing explanation for each selected item."""
        profiles = self.activity_store.profiles_for(uris) if self.activity_store else {}
        dimension_scores = self.activity_store.dimension_scores() if self.activity_store else {}
        favorites = self.favorites.read_only()
        now = datetime.datetime.now().timestamp()
        reasons: dict[str, str] = {}
        for uri in uris:
            profile = profiles.get(uri) or {}
            path = self.library.resolve_path(uri)
            media_type = str(profile.get('media_type') or '')
            if not media_type:
                media_type = 'video' if path and path.suffix.lower() in VIDEO_EXTENSIONS else 'image'
            if self.library.is_uri_in_set(uri, favorites):
                reasons[uri] = 'From your favorites'
            elif self.activity_store and any(
                dimension_scores.get(dimension, 0.0) >= 0.15
                for dimension in self.activity_store.dimensions_for(uri, media_type)
            ):
                reasons[uri] = 'Matches your browsing preferences'
            elif int(profile.get('impressions') or 0) == 0:
                reasons[uri] = 'Random discovery'
            else:
                try:
                    age_days = max((now - path.stat().st_mtime) / 86400, 0.0) if path else 999
                except OSError:
                    age_days = 999
                reasons[uri] = 'Recently added' if age_days <= 30 else 'Not viewed recently'
        return reasons

    @staticmethod
    def _recent_view_weight(value: object, now: datetime.datetime,
                            session_start: datetime.datetime | None = None) -> float:
        shown = _parse_shown_at(value)
        if not shown:
            return 1.0
        if session_start is not None and shown >= session_start:
            # Shown while the user is already scrolling this session: the feed must
            # keep its order, so do not down-weight it again (that reshuffled the
            # pool between pages and duplicated cards).
            return 1.0
        hours = max((now - shown).total_seconds() / 3600, 0.0)
        if hours < 6:
            return 0.12
        if hours < 48:
            return 0.35
        if hours < 24 * 14:
            return 0.7
        return 1.0
