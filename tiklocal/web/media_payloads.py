"""Shared media links and display fields; no IO or browsing policy."""

import datetime
from urllib.parse import quote


def media_urls(uri: str) -> dict[str, str]:
    return {
        'media_url': f"/media/{quote(uri, safe='/')}",
        'thumb_url': f"/thumb?uri={quote(uri)}",
    }


def post_date_of(record: dict | None) -> str:
    """The day a work was posted, ``YYYY-MM-DD``, or '' when the index has no idea.

    The export's own timestamp wins; a media library synced from a phone or an
    unzipped archive only carries a file mtime, and showing that beats showing
    nothing at all on the author line. Epoch zero is the exception: the index
    clamps an unrepresentable mtime there, and a caption dated 1970 is noise.
    """
    if not record:
        return ''
    captured = str(record.get('captured_local_date') or '').strip()[:10]
    if len(captured) == 10 and captured[4] == '-' and captured[7] == '-' and captured != '1970-01-01':
        return captured
    try:
        stamp = float(record.get('mtime_ts') or 0)
        moment = datetime.datetime.fromtimestamp(stamp)
    except (TypeError, ValueError, OSError, OverflowError):
        return ''
    if stamp <= 0 or moment.year <= 1970:
        return ''
    return moment.strftime('%Y-%m-%d')


def build_feed_media_item(name: str, media_type: str, *, live_video_uri: str = '') -> dict[str, str]:
    encoded = quote(name)
    detail_url = (f"/detail/{encoded}" if media_type == 'video' else
                  f"/media/{quote(name, safe='/')}" if media_type == 'audio' else
                  f"/image?uri={encoded}")
    item = {
        'type': media_type,
        'name': name,
        **media_urls(name),
        'detail_url': detail_url,
    }
    if media_type == 'live':
        # The cover is the entry; the paired mp4 plays on press-and-hold.
        item['is_live'] = True
        item['live_video_url'] = f"/media/{quote(live_video_uri, safe='/')}" if live_video_uri else ''
    return item


def serialize_library_item(record: dict) -> dict:
    name = str(record.get('name') or '')
    media_type = str(record.get('media_type') or 'video')
    item = {
        **build_feed_media_item(name, media_type, live_video_uri=str(record.get('live_video_uri') or '')),
        'mtime_ts': float(record.get('mtime_ts') or 0),
        'size_bytes': int(record.get('size_bytes') or 0),
        'width': record.get('width'),
        'height': record.get('height'),
    }
    if record.get('album_key'):
        item['album_key'] = str(record.get('album_key'))
        item['album_index'] = record.get('album_index')
        item['album_size'] = record.get('album_size')
    return item
