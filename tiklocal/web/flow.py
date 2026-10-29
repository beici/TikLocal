"""Flow HTTP entry points and media/card composition."""

import datetime
import random
import re
from pathlib import Path
from urllib.parse import quote

from flask import Response, render_template, request

from tiklocal.i18n import t
from tiklocal.services import media_naming
from tiklocal.services.recommendation import FORGOTTEN_DAYS, shown_within
from tiklocal.web import json_body, read_int_arg
from tiklocal.web.media_payloads import build_feed_media_item, post_date_of


def legacy_media_key(uri: str) -> str:
    text = str(uri or '').strip().replace('\\', '/')
    if text.startswith('@'):
        _, sep, rel_path = text[1:].partition('/')
        if sep and rel_path:
            return rel_path
    return text


def collect_source_media_groups(
    records: list[dict],
    download_source_store,
) -> list[dict]:
    records_by_name = {str(item.get('name') or ''): item for item in records if item.get('name')}
    record_lookup = dict(records_by_name)
    for name, record in records_by_name.items():
        record_lookup.setdefault(legacy_media_key(name), record)
    source_map = download_source_store.get_many(list(record_lookup))
    groups_by_source: dict[str, dict] = {}
    groups_by_job: dict[str, dict] = {}

    for name, source_meta in source_map.items():
        if not isinstance(source_meta, dict):
            continue
        record = record_lookup.get(name)
        if not record:
            continue
        canonical_name = str(record.get('name') or name)

        normalized_item = {
            'name': canonical_name,
            'media_type': str(record.get('media_type') or ''),
            'sort_ts': float(record.get('mtime_ts') or 0),
        }

        source_url = str(source_meta.get('source_url_display') or source_meta.get('source_url_raw') or '').strip()
        if source_url:
            group = groups_by_source.setdefault(source_url, {
                'key': source_url,
                'source_domain': str(source_meta.get('source_domain') or '').strip(),
                'created_at': str(source_meta.get('created_at') or '').strip(),
                'items': [],
            })
            group['items'].append(normalized_item)
            if str(source_meta.get('created_at') or '').strip() > str(group.get('created_at') or ''):
                group['created_at'] = str(source_meta.get('created_at') or '').strip()

        job_id = str(source_meta.get('job_id') or '').strip()
        if job_id:
            group = groups_by_job.setdefault(job_id, {
                'key': job_id,
                'source_domain': str(source_meta.get('source_domain') or '').strip(),
                'created_at': str(source_meta.get('created_at') or '').strip(),
                'items': [],
            })
            group['items'].append(normalized_item)
            if str(source_meta.get('created_at') or '').strip() > str(group.get('created_at') or ''):
                group['created_at'] = str(source_meta.get('created_at') or '').strip()

    results: list[dict] = []
    seen_group_signatures: set[tuple[str, ...]] = set()
    for group in list(groups_by_source.values()) + list(groups_by_job.values()):
        entries = group.get('items') or []
        unique_names = sorted({str(item.get('name') or '') for item in entries if item.get('name')})
        if len(unique_names) < 2:
            continue
        signature = tuple(unique_names)
        if signature in seen_group_signatures:
            continue
        seen_group_signatures.add(signature)

        sorted_items = sorted(
            [item for item in entries if item.get('name') in records_by_name],
            key=lambda item: (float(item.get('sort_ts') or 0), str(item.get('name') or '')),
            reverse=True,
        )
        domain = str(group.get('source_domain') or '').strip()
        results.append({
            'source_domain': domain,
            'created_at': str(group.get('created_at') or ''),
            'items': [
                build_feed_media_item(str(item['name']), str(item['media_type']))
                for item in sorted_items[:8]
            ],
        })

    results.sort(
        key=lambda item: (
            str(item.get('created_at') or ''),
            len(item.get('items') or []),
        ),
        reverse=True,
    )
    return results


def build_author_caption(name: str, sources_by_id: dict | None = None, record: dict | None = None) -> dict:
    """Derive the Douyin-style author handle and post caption from a media URI.

    A media source is normally one blogger export folder, so the source carries
    the author and the file name carries the post title; blogger folders parked
    inside a shared default root are recognised by their path segment instead.
    """
    text = str(name or '').strip().replace('\\', '/')
    if not text:
        return {}

    source_id = ''
    rel_path = text
    if text.startswith('@'):
        source_id, sep, rel_path = text[1:].partition('/')
        if not sep or not source_id:
            return {}
    segments = [segment for segment in rel_path.split('/') if segment]
    caption = media_naming.post_title(segments[-1]) if segments else ''

    folder = ''
    if source_id:
        source = (sources_by_id or {}).get(source_id)
        if len(segments) > 1:
            folder = segments[0]
        elif source is not None:
            path = str(getattr(source, 'path', '') or '')
            folder = Path(path).name if path not in {'', '.', '/'} else str(getattr(source, 'name', '') or '')
        else:
            folder = source_id
        if source_id == 'default' and len(segments) <= 1 and not media_naming.is_blogger_folder(folder):
            # Loose files under a generic media root carry no author of their own.
            # A default root that is itself a Douyin export folder does name one, so
            # keep it: that is the common ``tiklocal <UID…_博主名_发布作品>`` setup.
            folder = ''
    elif len(segments) >= 2:
        folder = segments[0]

    item: dict = {}
    if folder:
        author = media_naming.blogger_display_name(folder)
        if author:
            item['author_name'] = author
            item['author_url'] = f"/flow?blog={quote(source_id or folder, safe='')}"
    if caption:
        item['caption_text'] = caption
    post_date = post_date_of(record)
    if post_date:
        item['post_date'] = post_date
    return item


def build_memory_card(records: list[dict], profiles: dict, rng: random.Random, now: datetime.datetime) -> dict | None:
    """Images from one past week that have not been shown for a while; callers pass year-old images."""
    weeks: dict[tuple[int, int], list[dict]] = {}
    for record in records:
        if shown_within(profiles.get(record['name']), FORGOTTEN_DAYS, now):
            continue
        week = datetime.date.fromtimestamp(record['mtime_ts']).isocalendar()[:2]
        weeks.setdefault(week, []).append(record)
    weeks = {week: items for week, items in weeks.items() if len(items) >= 3}
    if not weeks:
        return None
    # The same week in an earlier year reads as an anniversary; otherwise any week will do.
    same_week = sorted(week for week in weeks if week[1] == now.isocalendar()[1])
    items = weeks[rng.choice(same_week or sorted(weeks))]
    if len(items) > 8:
        items = rng.sample(items, 8)
    items.sort(key=lambda item: item['mtime_ts'])
    first = datetime.date.fromtimestamp(items[0]['mtime_ts'])
    return {
        'type': 'image_group',
        'name': f"memory:{items[0]['name']}",
        'caption': (
            t('flow.memory.this_week', year=first.year) if same_week else
            t('flow.memory.month_year', year=first.year, month=first.month,
              month_name=first.strftime('%B'))
        ),
        'items': [build_feed_media_item(item['name'], 'image') for item in items],
    }


def normalize_tag(tag: str) -> str:
    """``#海边`` / `` #海边 `` → ``海边``; '' when nothing is left."""
    return str(tag or '').strip().lstrip('#').strip()


def tag_matches(name: str, tag: str) -> bool:
    """A hashtag matches when the caption the chrome shows contains it, ignoring case."""
    if not tag:
        return False
    return tag.casefold() in media_naming.post_title(name).casefold()


def caption_matches(name: str, caption: str) -> bool:
    """A caption search narrows a timeline the same way a hashtag narrows the feed."""
    needle = str(caption or '').strip().casefold()
    if not needle:
        return False
    return needle in media_naming.post_title(name).casefold()


def post_kind_label(name: str, media_type: str = '', album_size: int = 0) -> str:
    """Douyin's small tag after the handle: 图文 / 视频 / 实况 / 图集.

    A grouped post is named by its group (a live post and a gallery post look
    identical on disk), a lone row by its own media type, and a bare photo keeps
    Douyin's own word for a photo post.
    """
    stem = str(name or '').rsplit('/', 1)[-1].rsplit('.', 1)[0]
    if album_size > 1 or media_naming.album_member(str(name).rsplit('/', 1)[-1]):
        if media_naming.is_live_stem(stem) or media_type == 'live':
            return t('flow.kind.live')
        return t('flow.kind.album')
    if media_type == 'video':
        return t('flow.kind.video')
    if media_type == 'live':
        return t('flow.kind.live')
    return t('flow.kind.image')


def collapse_media_entries(
    page_media: list[tuple[str, str]],
    records_by_name: dict,
    media_index,
    stop_after: int | None = None,
) -> list[dict]:
    """Fold album members and live posts into the entries the feed renders.

    ``stop_after`` ends the walk once that many entries exist. Every album cover costs
    a query for its members, so folding a whole 20k library to render page three spent
    thousands of queries and tens of MB on entries the caller slices away again.
    """
    entries: list[dict] = []
    for media_type, name in page_media:
        if stop_after is not None and len(entries) >= stop_after:
            break
        record = records_by_name.get(name)
        if record and record.get('album_key'):
            # Group members only surface through their cover's swipeable entry.
            if int(record.get('album_index') or 0) != 0:
                continue
            members = media_index.album_members(str(record['album_key']))
            if len(members) <= 1:
                # A lone member is just itself; its row type already says which.
                entries.append({'type': str(record.get('media_type') or media_type), 'name': name})
                continue
            is_live_post = media_naming.is_live_stem(name.rsplit('/', 1)[-1].rsplit('.', 1)[0])
            entries.append({
                'type': 'image_group',
                'name': f"album:{name}",
                'title': t('flow.group.live_title') if is_live_post else t('flow.group.album_title'),
                'subtitle': t('flow.group.album_subtitle'),
                'items': [
                    build_feed_media_item(
                        str(member['name']), str(member['media_type']),
                        live_video_uri=str(member.get('live_video_uri') or ''),
                    )
                    for member in members
                ],
            })
            continue
        if record and str(record.get('media_type') or '') == 'live':
            entries.append({'type': 'live', 'name': name})
            continue
        entries.append({'type': media_type, 'name': name})
    return entries


def build_feed_items(
    entries: list[dict],
    records_by_name: dict,
    sources_by_id: dict | None = None,
    recommendation_reasons: dict | None = None,
) -> list[dict]:
    """Render feed entries as payload items, with the author caption on every item."""
    items = []
    for entry in entries:
        item_type = str(entry.get('type') or '')
        if item_type == 'image_group':
            group_item = {**entry, 'items': [dict(child) for child in entry['items']]}
            group_name = str(entry.get('name') or '')
            if group_name.startswith('album:'):
                cover_name = group_name[len('album:'):]
                group_item.update(build_author_caption(
                    cover_name, sources_by_id, records_by_name.get(cover_name),
                ))
            elif group_name.startswith('group:'):
                # An original-export group card names the work it was downloaded
                # from, so it dates like the cover rather than like nothing.
                cover_name = group_name[len('group:'):]
                group_item.update(build_author_caption(
                    cover_name, sources_by_id, records_by_name.get(cover_name),
                ))
            group_item['kind_label'] = post_kind_label(
                str(group_item.get('name') or '').split(':', 1)[-1], 'image',
                len(group_item.get('items') or []),
            )
            items.append(group_item)
            continue

        name = str(entry.get('name') or '')
        if not name or item_type not in {'video', 'image', 'live'}:
            continue
        record = records_by_name.get(name) or {}
        item = build_feed_media_item(
            name, item_type,
            live_video_uri=str(record.get('live_video_uri') or ''),
        )
        if recommendation_reasons is not None:
            item['recommendation_reason'] = recommendation_reasons.get(name, '')
        item.update(build_author_caption(name, sources_by_id, record))
        item['kind_label'] = post_kind_label(name, item_type, int(record.get('album_size') or 0))
        items.append(item)
    return items


def build_tag_feed_page(
    *,
    tag: str,
    page: int,
    size: int,
    seed: str,
    media_index,
    sources_by_id: dict | None = None,
    blog: str = '',
) -> dict:
    """One caption hashtag, newest first: no weighting, no memory, no extra cards."""
    records = media_index.records(source_id=blog)
    records_by_name = {str(record.get('name') or ''): record for record in records}
    matched = [
        (str(record.get('media_type') or ''), str(record.get('name') or ''))
        for record in records
        if tag_matches(str(record.get('name') or ''), tag)
    ]
    end = page * size
    start = max(0, end - size)
    # One entry past the slice is enough to answer ``has_more``.
    entries = collapse_media_entries(matched, records_by_name, media_index, stop_after=end + 1)
    return {
        'items': build_feed_items(entries[start:end], records_by_name, sources_by_id),
        'page': page,
        'has_more': len(entries) > end,
        'seed': seed,
        'tag': tag,
    }


def build_start_feed_page(
    *,
    uri: str,
    page: int,
    size: int,
    seed: str,
    media_index,
    sources_by_id: dict | None = None,
    blog: str = '',
    caption: str = '',
) -> dict:
    """Open one specific work and keep scrolling down from there.

    A deep link (author page, search, a shared link) must land on the work the
    user asked for even when it is far outside the first recommendation page,
    so the timeline is rebuilt deterministically and sliced from that work.

    ``blog`` keeps scrolling one author's timeline and ``caption`` keeps
    scrolling the works that share a caption, which is what a search result is
    expected to continue into. A caption that no longer contains ``uri`` (the
    work was retitled after the search) falls back to the unfiltered timeline
    instead of opening an empty feed.
    """
    source_id = str(blog or '').strip()
    query = str(caption or '').strip()
    if not source_id and uri:
        match = re.match(r'^@([^/]+)/', uri)
        source_id = match.group(1) if match else ''
    if source_id:
        records = media_index.records(source_id=source_id)
    elif query:
        # A caption-only feed is library-wide; a deep link with no source prefix
        # still only needs the one work, which the offset search below finds.
        records = media_index.records()
    else:
        records = []
    records_by_name = {str(record.get('name') or ''): record for record in records}
    pairs = [
        (str(record.get('media_type') or ''), str(record.get('name') or ''))
        for record in records
    ]

    def entry_offset(candidate_entries: list[dict]) -> int | None:
        for position, entry in enumerate(candidate_entries):
            names = [str(entry.get('name') or '')]
            names.extend(str(child.get('name') or '') for child in entry.get('items') or [])
            if uri in names:
                return position
        return None

    entries = collapse_media_entries(pairs, records_by_name, media_index)
    offset = entry_offset(entries) if uri else 0
    if query:
        narrowed = [pair for pair in pairs if caption_matches(pair[1], query)]
        narrowed_entries = collapse_media_entries(narrowed, records_by_name, media_index)
        narrowed_offset = entry_offset(narrowed_entries) if uri else 0
        # Only adopt the narrowed timeline when the requested work is still in it:
        # a retitled post should open the unfiltered feed rather than an empty one.
        if not uri or narrowed_offset is not None:
            entries, offset = narrowed_entries, narrowed_offset
    entries = entries[offset or 0:]
    end = page * size
    start = max(0, end - size)
    return {
        'items': build_feed_items(entries[start:end], records_by_name, sources_by_id),
        'page': page,
        'has_more': len(entries) > end,
        'seed': seed,
        'start': uri,
        'caption': query,
    }


def build_mix_feed_page(
    *,
    page: int,
    size: int,
    seed: str,
    recommend_service,
    media_index,
    download_source_store,
    activity_store=None,
    related_photo_groups=None,
    mode: str = '',
    blog: str = '',
    sources_by_id: dict | None = None,
    tag: str = '',
    start: str = '',
    caption: str = '',
    session_start=None,
) -> dict:
    raw_tag = str(tag or '').strip()
    tag = normalize_tag(raw_tag)
    if tag:
        return build_tag_feed_page(
            tag=tag, page=page, size=size, seed=seed,
            media_index=media_index, sources_by_id=sources_by_id, blog=blog,
        )
    if raw_tag:
        # A lone '#' names no tag: filtering still applies and nothing matches.
        return {'items': [], 'page': page, 'has_more': False, 'seed': seed, 'tag': ''}

    raw_start = str(start or '').strip()
    if raw_start:
        return build_start_feed_page(
            uri=raw_start, page=page, size=size, seed=seed,
            media_index=media_index, sources_by_id=sources_by_id, blog=blog,
            caption=caption,
        )

    if str(caption or '').strip():
        # A caption filter without a work to start from still means "only these
        # captions": a shared /flow?caption= link must not fall back to the whole
        # recommendation pool.
        return build_start_feed_page(
            uri='', page=page, size=size, seed=seed,
            media_index=media_index, sources_by_id=sources_by_id, blog=blog,
            caption=caption,
        )

    video_ratio = 4
    image_ratio = 1

    ratio_total = video_ratio + image_ratio
    end = page * size
    start = max(0, end - size)
    # Either media type must be able to fill the page when the other runs out.
    request_window = end + 1

    forgotten = mode == 'forgotten'
    select = recommend_service.get_forgotten_selection if forgotten else recommend_service.get_weighted_selection
    # Only pass the session cutoff when there is one: injected/legacy recommend
    # services (tests, alternative implementations) predate the keyword.
    session_kwargs = {'session_start': session_start} if session_start is not None else {}
    videos = select(
        file_type='video',
        limit=request_window,
        seed=f"{seed}:video",
        source_id=blog,
        **session_kwargs,
    )
    images = select(
        file_type='image',
        limit=request_window,
        seed=f"{seed}:image",
        source_id=blog,
        **session_kwargs,
    )

    target_image_prob = image_ratio / max(1, ratio_total)
    max_video_streak = 6
    max_image_streak = 2
    rng = random.Random(f"{seed}:mix")

    mixed: list[tuple[str, str]] = []
    seen: set[str] = set()
    vi = 0
    ii = 0
    used_video = 0
    used_image = 0
    video_streak = 0
    image_streak = 0

    def pick_next_available(kind: str) -> tuple[str, str]:
        nonlocal vi, ii
        if kind == 'video':
            while vi < len(videos):
                name = videos[vi]
                vi += 1
                if name:
                    return 'video', name
            return '', ''
        while ii < len(images):
            name = images[ii]
            ii += 1
            if name:
                return 'image', name
        return '', ''

    while len(mixed) < request_window and (vi < len(videos) or ii < len(images)):
        if video_streak >= max_video_streak and ii < len(images):
            want_type = 'image'
        elif image_streak >= max_image_streak and vi < len(videos):
            want_type = 'video'
        else:
            total_used = used_video + used_image
            current_image_ratio = (used_image / total_used) if total_used > 0 else target_image_prob
            correction = (target_image_prob - current_image_ratio) * 0.65
            p_image = max(0.05, min(0.5, target_image_prob + correction))
            want_type = 'image' if rng.random() < p_image else 'video'

        chosen_type, chosen_name = pick_next_available(want_type)
        if not chosen_name:
            fallback = 'video' if want_type == 'image' else 'image'
            chosen_type, chosen_name = pick_next_available(fallback)

        if not chosen_name or chosen_name in seen:
            continue
        seen.add(chosen_name)
        mixed.append((chosen_type, chosen_name))
        if chosen_type == 'video':
            used_video += 1
            video_streak += 1
            image_streak = 0
        else:
            used_image += 1
            image_streak += 1
            video_streak = 0

    page_media = mixed[start:end]
    page_names = {name for _, name in page_media}
    # The forgotten feed stays plain media; cards belong to the everyday Flow.
    # Only the page's own URIs are looked up: a full table read cost 400ms per
    # request on an 8000-file library, and the feed asks for ~24 cards. Lightweight
    # index stand-ins (tests) may only implement records(), so fall back to that.
    if forgotten:
        records = []
    elif hasattr(media_index, 'records_for_names'):
        records = media_index.records_for_names(page_names)
    else:
        records = [record for record in media_index.records() if record.get('name') in page_names]
    records_by_name = {str(record.get('name') or ''): record for record in records}
    source_groups = collect_source_media_groups(records, download_source_store) if records else []
    image_group_candidate = None
    image_group_names: set[str] = set()
    for group in source_groups:
        group_items = [item for item in (group.get('items') or []) if item.get('type') == 'image']
        if len(group_items) < 2:
            continue
        image_group_candidate = {
            'type': 'image_group',
            'name': f"group:{group_items[0]['name']}",
            'title': t('flow.group.original_title'),
            'subtitle': t('flow.group.original_subtitle'),
            'items': group_items,
        }
        image_group_names = {str(item.get('name') or '') for item in group_items}
        break

    mixed_entries = collapse_media_entries(page_media, records_by_name, media_index)
    if image_group_candidate and mixed_entries:
        mixed_entries = [
            entry for entry in mixed_entries
            if not (entry.get('type') == 'image' and str(entry.get('name') or '') in image_group_names)
        ]
        group_rng = random.Random(f"{seed}:image-group")
        insert_floor = min(2, len(mixed_entries))
        insert_ceil = min(max(insert_floor, 6), len(mixed_entries))
        insert_at = insert_floor if insert_ceil <= insert_floor else group_rng.randint(insert_floor, insert_ceil)
        mixed_entries.insert(insert_at, image_group_candidate)
    # A memory every other page keeps it a small surprise rather than a pattern.
    memory = None
    if records and page % 2 == 1:
        now = datetime.datetime.now()
        year_ago = now.timestamp() - 365 * 86400
        old_images = [
            record for record in records
            if record.get('media_type') == 'image' and 0 < float(record.get('mtime_ts') or 0) <= year_ago
        ]
        profiles = activity_store.profiles_for([record['name'] for record in old_images]) if activity_store else {}
        memory = build_memory_card(old_images, profiles, random.Random(f"{seed}:memory:{page}"), now)
    if memory and mixed_entries:
        memory_names = {child['name'] for child in memory['items']}
        mixed_entries = [entry for entry in mixed_entries if entry['name'] not in memory_names]
        memory_rng = random.Random(f"{seed}:memory-slot:{page}")
        insert_floor = min(8, len(mixed_entries))
        insert_ceil = min(max(insert_floor, 14), len(mixed_entries))
        insert_at = insert_floor if insert_ceil <= insert_floor else memory_rng.randint(insert_floor, insert_ceil)
        mixed_entries.insert(insert_at, memory)

    # Reuse the gallery slot on even pages; keep existing source and memory cards first.
    if (not forgotten and page % 2 == 0 and mixed_entries and
            not image_group_candidate and not memory and related_photo_groups):
        available = related_photo_groups(limit=8).get('items') or []
        if available:
            group = available[(page // 2 - 1) % len(available)]
            children = [build_feed_media_item(item['uri'], 'image') for item in group['items'][:8]]
            child_names = {str(item.get('name') or '') for item in children}
            mixed_entries = [entry for entry in mixed_entries
                             if entry.get('type') != 'image' or entry.get('name') not in child_names]
            insert_at = min(6, len(mixed_entries))
            mixed_entries.insert(insert_at, {
                'type': 'image_group', 'name': f"related:{group['group_key']}",
                'title': t('flow.group.related_title'), 'subtitle': t('flow.group.related_subtitle'),
                'items': children,
            })

    recommendation_reasons = recommend_service.reasons_for([
        str(entry.get('name') or '')
        for entry in mixed_entries
        if entry.get('type') in {'video', 'image', 'live'}
    ])
    items = build_feed_items(mixed_entries, records_by_name, sources_by_id, recommendation_reasons)

    return {
        'items': items,
        'page': page,
        'has_more': len(mixed) > end,
        'seed': seed,
        # Echo the session marker so the next page ranks against the same cutoff
        # instead of the impressions this page just created.
        'since': session_start.timestamp() if session_start is not None else None,
    }


def register_flow_routes(
    app, *, media_index, recommend_service,
    download_source_store, activity_store, related_photo_groups=None,
    library_service=None,
):
    @app.route('/flow')
    def flow_view():
        # The feed controller reads tag/start/caption from the URL itself, so passing
        # them into the template only looked like they were being used.
        return render_template('flow.html', menu='flow')

    @app.route('/api/feed/mix')
    def api_feed_mix():
        # Flask maps HEAD onto the GET view and only drops the body afterwards, so a
        # probe or a player preflight used to pay for the whole candidate pool and
        # JSON serialisation (139 ms on an 8k library) just to learn nothing.
        if request.method == 'HEAD':
            return Response(status=200, mimetype='application/json')
        # The first request of a feed session pins the ranking cutoff; every later
        # page echoes it back, so cards already shown are not re-ranked (which made
        # page 2 repeat page 1 and permanently skip the items pushed up).
        since_raw = request.args.get('since')
        try:
            session_start = (datetime.datetime.fromtimestamp(float(since_raw))
                             if since_raw else datetime.datetime.now())
        except (TypeError, ValueError, OverflowError, OSError):
            session_start = datetime.datetime.now()
        result = build_mix_feed_page(
            page=read_int_arg('page', 1, minimum=1, maximum=5000),
            size=read_int_arg('size', 24, minimum=8, maximum=200),
            seed=request.args.get('seed') or str(random.randint(1, 999999)),
            recommend_service=recommend_service,
            media_index=media_index,
            download_source_store=download_source_store,
            activity_store=activity_store,
            related_photo_groups=related_photo_groups,
            mode=request.args.get('mode', ''),
            blog=str(request.args.get('blog', '')).strip(),
            sources_by_id=getattr(library_service, 'sources_by_id', None) or {},
            tag=request.args.get('tag', ''),
            start=request.args.get('start', ''),
            caption=request.args.get('caption', ''),
            session_start=session_start,
        )
        if request.args.get('snapshot') == '1':
            result['has_more'] = False
        return result

    @app.route('/api/activity', methods=['POST', 'DELETE'])
    def api_activity():
        if request.method == 'DELETE':
            activity_store.clear()
            return {'success': True}
        payload = json_body()
        events = payload.get('events') if isinstance(payload.get('events'), list) else [payload]
        accepted = activity_store.record_many(events)
        return {'success': True, 'data': {'accepted': accepted}}
