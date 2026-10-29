import datetime
import json
import random
import subprocess as sp
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from collections import OrderedDict
from typing import Any, Callable

from tiklocal.services.json_storage import read_json_file, write_json_atomic
from tiklocal.services.library import LibraryService
from tiklocal.services.favorites import FavoriteService
from tiklocal.services.thumbnail import banner_duration, ffprobe_argv


def _positive_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


@dataclass(frozen=True)
class RadioStation:
    id: str
    name: str
    description: str


@dataclass(frozen=True)
class RadioCandidate:
    path: Path
    name: str
    title: str
    artist: str
    album: str
    duration: float | None
    parent_key: str
    is_favorite: bool
    mtime: float


@dataclass(frozen=True)
class AudioMetadata:
    title: str = ""
    artist: str = ""
    album: str = ""
    duration: float | None = None


#: Guards the profile's read-modify-write; the player sends skip and play almost
#: simultaneously and a lost update would drop whole tracks from the profile.
_PROFILE_LOCK = threading.Lock()

#: Metadata probe results are small, but the cache is keyed by absolute path: a
#: 20k track library used to keep 20k entries for the life of the process.
METADATA_CACHE_LIMIT = 2000


class RadioProfileStore:
    """Persist light listening feedback for local radio selection."""

    event_weights = {
        "play": 0.02,
        "complete": 0.16,
        "replay": 0.18,
        "favorite": 0.30,
        "skip": -0.09,
        "error": -0.18,
    }

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, dict[str, Any]]:
        # A half-written profile used to read as {} and the next play event saved
        # over it, losing every score the user had accumulated. Quarantine instead.
        payload = read_json_file(self.path, {})
        tracks = payload.get("tracks") if isinstance(payload, dict) else None
        return tracks if isinstance(tracks, dict) else {}

    def record(self, uri: str, event: str, *, ratio: float | None = None) -> dict[str, Any]:
        if not uri:
            return {}
        event = event if event in self.event_weights else "play"
        # The player fires skip/play back to back, so two requests can load, edit
        # and save the same profile at once; serialise the read-modify-write or the
        # later save silently drops every track the other request added.
        with _PROFILE_LOCK:
            tracks = self.load()
            entry = tracks.get(uri) if isinstance(tracks.get(uri), dict) else {}

            entry["plays"] = int(entry.get("plays") or 0) + (1 if event == "play" else 0)
            entry["completes"] = int(entry.get("completes") or 0) + (1 if event == "complete" else 0)
            entry["replays"] = int(entry.get("replays") or 0) + (1 if event == "replay" else 0)
            entry["skips"] = int(entry.get("skips") or 0) + (1 if event == "skip" else 0)
            entry["errors"] = int(entry.get("errors") or 0) + (1 if event == "error" else 0)
            if ratio is not None:
                entry["last_ratio"] = max(0.0, min(float(ratio), 1.0))
            entry["last_event"] = event
            entry["last_played_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            entry["score"] = self._next_score(float(entry.get("score") or 0.0), event, ratio)

            tracks[uri] = entry
            self._save(tracks)
        return entry

    def _next_score(self, current: float, event: str, ratio: float | None) -> float:
        delta = self.event_weights[event]
        if event == "complete" and ratio is not None:
            delta += max(0.0, min(ratio, 1.0)) * 0.08
        if event == "skip" and ratio is not None and ratio < 0.18:
            delta -= 0.05
        return max(-0.8, min(current + delta, 1.4))

    def _save(self, tracks: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        trimmed = dict(sorted(
            tracks.items(),
            key=lambda item: str(item[1].get("last_played_at") or ""),
            reverse=True,
        )[:1200])
        write_json_atomic(self.path, {"tracks": trimmed})


class RadioService:
    """Build low-decision radio batches from local audio files."""

    stations = [
        RadioStation("default", "Default Station", "A mix of recently added and favorite tracks"),
        RadioStation("recent", "Recently Added", "Prioritizes newly added audio"),
        RadioStation("favorites", "Favorites", "Prioritizes favorite audio"),
    ]

    def __init__(
        self,
        library_service: LibraryService,
        favorite_service: FavoriteService,
        media_index,
        profile_store: RadioProfileStore | None = None,
        activity_store=None,
    ):
        self.library = library_service
        self.favorites = favorite_service
        self.media_index = media_index
        self.profile_store = profile_store
        self.activity_store = activity_store
        # Bounded LRU: the key is an absolute path, so an unbounded dict kept one
        # entry per audio file ever probed (and never released a deleted track).
        self._metadata_cache: OrderedDict[str, tuple[float, AudioMetadata]] = OrderedDict()

    def list_stations(self) -> list[dict]:
        return [
            {"id": station.id, "name": station.name, "description": station.description}
            for station in self.stations
        ]

    def record_feedback(self, uri: str, event: str, *, ratio: float | None = None) -> dict[str, Any]:
        canonical = self.library.canonicalize_uri(uri)
        if self.activity_store:
            unified_event = "impression" if event == "play" else event
            self.activity_store.record_many([{
                "uri": canonical,
                "media_type": "audio",
                "surface": "radio",
                "event": unified_event,
                "ratio": ratio,
            }])
        if not self.profile_store:
            return {}
        return self.profile_store.record(canonical, event, ratio=ratio)

    def tune(
        self,
        *,
        station: str = "default",
        limit: int = 12,
        exclude: set[str] | None = None,
        seed: str | None = None,
        serialize_track: Callable[[RadioCandidate], dict],
    ) -> dict:
        station_id = station if station in {item.id for item in self.stations} else "default"
        candidates = self.collect_candidates()
        excluded = set(exclude or set())
        available = [item for item in candidates if item.name not in excluded]
        if not available and candidates:
            available = candidates

        rng = random.Random(seed) if seed else random.Random()
        profile = self.profile_store.load() if self.profile_store else {}
        if station_id == "recent":
            selected = self._select_recent(available, limit, rng)
        elif station_id == "favorites":
            selected = self._select_favorites(available, candidates, excluded, limit, rng, profile)
        else:
            selected = self._select_weighted(available, limit, rng, profile)

        station_meta = next(item for item in self.list_stations() if item["id"] == station_id)
        return {
            "station": station_meta,
            "items": [serialize_track(item) for item in selected],
            "total": len(candidates),
            "available": len(available),
        }

    def collect_candidates(self) -> list[RadioCandidate]:
        favs = self.favorites.load()
        candidates: list[RadioCandidate] = []
        for record in self.media_index.records(media_type='audio'):
            try:
                name = record['name']
                path = self.library.existing_media_path(name)
                if path is None:
                    continue
                parent = self.library.relative_path_for_uri(name).rsplit("/", 1)[0]
                candidates.append(
                    RadioCandidate(
                        path=path,
                        name=name,
                        title=path.stem,
                        artist="",
                        album="",
                        duration=None,
                        parent_key=parent,
                        is_favorite=self.library.is_uri_in_set(name, favs),
                        mtime=record['mtime_ts'],
                    )
                )
            except OSError:
                continue
        return candidates

    def metadata_for_uri(self, uri: str) -> AudioMetadata:
        path = self.library.resolve_path(uri)
        if not path or not path.exists() or not path.is_file():
            return AudioMetadata()
        return self.metadata_for(path)

    def metadata_for(self, path: Path) -> AudioMetadata:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return AudioMetadata()

        cache_key = str(path)
        cached = self._metadata_cache.get(cache_key)
        if cached and cached[0] == mtime:
            self._metadata_cache.move_to_end(cache_key)
            return cached[1]

        metadata = self._probe_audio_metadata(path)
        self._metadata_cache[cache_key] = (mtime, metadata)
        self._metadata_cache.move_to_end(cache_key)
        while len(self._metadata_cache) > METADATA_CACHE_LIMIT:
            self._metadata_cache.popitem(last=False)
        return metadata

    def metadata_for_many(self, paths: list[Path]) -> list[AudioMetadata]:
        """Resolve metadata for a batch; cold cache entries are probed concurrently.

        Probing one by one made the radio page wait for a full ffprobe round trip per
        track (200 tracks ≈ seconds) whenever the process had just started.
        """
        if len(paths) <= 1:
            return [self.metadata_for(path) for path in paths]
        with ThreadPoolExecutor(max_workers=min(8, len(paths)), thread_name_prefix="radio-probe") as pool:
            return list(pool.map(self.metadata_for, paths))

    def _probe_audio_metadata(self, path: Path) -> AudioMetadata:
        # Prefer the bundled/resolved binary; a system ffprobe that the resolver
        # missed is still worth one attempt.
        probe = ffprobe_argv() or ["ffprobe"]
        if probe:
            try:
                proc = sp.run(
                    [
                        *probe,
                        "-v", "error",
                        "-show_entries", "format=duration:format_tags=title,artist,album",
                        "-of", "json",
                        str(path),
                    ],
                    capture_output=True,
                    text=True,
                    # ID3v2 tags are UTF-8/UTF-16 but ffprobe's JSON writer emits
                    # UTF-8; decoding with the Windows ANSI code page raised
                    # UnicodeDecodeError (a ValueError, swallowed below) and every
                    # Chinese title fell back to the file name.
                    encoding="utf-8",
                    errors="replace",
                    timeout=5,
                )
                if proc.returncode == 0:
                    payload = json.loads(proc.stdout or "{}")
                    fmt = payload.get("format") if isinstance(payload, dict) else {}
                    fmt = fmt if isinstance(fmt, dict) else {}
                    tags = fmt.get("tags") if isinstance(fmt.get("tags"), dict) else {}
                    normalized_tags = {str(key).lower(): str(value).strip() for key, value in tags.items()}
                    duration = _positive_float(fmt.get("duration"))
                    return AudioMetadata(
                        title=normalized_tags.get("title", ""),
                        artist=normalized_tags.get("artist", ""),
                        album=normalized_tags.get("album", ""),
                        duration=duration,
                    )
            except (OSError, sp.SubprocessError, ValueError):
                pass
        # The bundled imageio-ffmpeg build ships no ffprobe; ffmpeg still reports the
        # duration in its banner, which beats showing no length at all.
        return AudioMetadata(duration=banner_duration(path))

    def _select_recent(
        self,
        candidates: list[RadioCandidate],
        limit: int,
        rng: random.Random,
    ) -> list[RadioCandidate]:
        pool = sorted(candidates, key=lambda item: item.mtime, reverse=True)[: max(limit * 8, 40)]
        return self._spread_by_parent(pool, limit, rng)

    def _select_favorites(
        self,
        available: list[RadioCandidate],
        all_candidates: list[RadioCandidate],
        excluded: set[str],
        limit: int,
        rng: random.Random,
        profile: dict[str, dict[str, Any]],
    ) -> list[RadioCandidate]:
        favorites = [item for item in available if item.is_favorite]
        selected = self._spread_by_parent(favorites, limit, rng)
        if len(selected) >= limit:
            return selected

        favorite_parents = {item.parent_key for item in favorites}
        related = [
            item for item in available
            if item.name not in {chosen.name for chosen in selected}
            and item.parent_key in favorite_parents
        ]
        selected.extend(self._spread_by_parent(related, limit - len(selected), rng))
        if len(selected) >= limit:
            return selected

        fallback = [
            item for item in all_candidates
            if item.name not in excluded and item.name not in {chosen.name for chosen in selected}
        ]
        selected.extend(self._select_weighted(fallback, limit - len(selected), rng, profile))
        return selected

    def _select_weighted(
        self,
        candidates: list[RadioCandidate],
        limit: int,
        rng: random.Random,
        profile: dict[str, dict[str, Any]] | None = None,
    ) -> list[RadioCandidate]:
        now = datetime.datetime.now().timestamp()
        profile = profile or {}
        weighted: list[tuple[RadioCandidate, float]] = []
        for item in candidates:
            age_days = max((now - item.mtime) / 86400, 0.0)
            recency = 2.0 if age_days <= 30 else max(0.2, 1.0 - min(age_days, 365) / 365)
            favorite = 3.0 if item.is_favorite else 1.0
            learned = self._profile_weight(profile.get(item.name))
            weighted.append((item, favorite * recency * learned))

        selected: list[RadioCandidate] = []
        recent_parents: list[str] = []
        pool = weighted[:]
        while pool and len(selected) < limit:
            parent_counts = {parent: recent_parents.count(parent) for parent in set(recent_parents)}
            usable = [
                (item, weight) for item, weight in pool
                if parent_counts.get(item.parent_key, 0) < 2 or len(pool) <= 2
            ]
            if not usable:
                usable = pool
            picked = self._weighted_pick(usable, rng)
            selected.append(picked)
            recent_parents = (recent_parents + [picked.parent_key])[-2:]
            pool = [(item, weight) for item, weight in pool if item.name != picked.name]
        return selected

    def _profile_weight(self, entry: dict[str, Any] | None) -> float:
        if not entry:
            return 1.0
        score = float(entry.get("score") or 0.0)
        skips = int(entry.get("skips") or 0)
        completes = int(entry.get("completes") or 0)
        replays = int(entry.get("replays") or 0)
        confidence = min(skips + completes + replays, 8) / 8
        return max(0.25, min(2.2, 1.0 + score * (0.45 + confidence * 0.55)))

    def _spread_by_parent(
        self,
        candidates: list[RadioCandidate],
        limit: int,
        rng: random.Random,
    ) -> list[RadioCandidate]:
        pool = candidates[:]
        rng.shuffle(pool)
        selected: list[RadioCandidate] = []
        recent_parents: list[str] = []
        while pool and len(selected) < limit:
            idx = next(
                (
                    index for index, item in enumerate(pool)
                    if recent_parents.count(item.parent_key) < 2
                ),
                0,
            )
            picked = pool.pop(idx)
            selected.append(picked)
            recent_parents = (recent_parents + [picked.parent_key])[-2:]
        return selected

    def _weighted_pick(
        self,
        weighted: list[tuple[RadioCandidate, float]],
        rng: random.Random,
    ) -> RadioCandidate:
        total = sum(max(weight, 0.01) for _, weight in weighted)
        pick = rng.random() * total
        current = 0.0
        for item, weight in weighted:
            current += max(weight, 0.01)
            if current >= pick:
                return item
        return weighted[-1][0]
