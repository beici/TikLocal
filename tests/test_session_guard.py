"""The read-only favorites view and the session guard's load order.

Two per-request costs that used to compound on the feed: every page of the feed
parsed and canonicalized ``favorites.json`` twice more than it needed to, and the
401 wrapper was installed by ``base.js`` at the end of ``<body>``, which is after
the page controllers have already fired their first-paint requests.
"""
import json
import os
import re

from tiklocal.app import create_app
from tiklocal.services.favorites import FavoriteService
from tiklocal.services.library import LibraryService


def _library(tmp_path):
    media = tmp_path / 'media'
    media.mkdir()
    (media / 'photo.jpg').write_bytes(b'jpeg')
    return LibraryService(media)


def _write(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries), encoding='utf-8')
    # The cache is keyed on mtime+size; a filesystem with a coarse clock could
    # otherwise hand two different writes the same stamp and hide the invalidation.
    stamp = path.stat().st_mtime_ns + 1_000_000
    os.utime(path, ns=(stamp, stamp))


def test_read_only_reuses_one_parse_until_the_file_changes(tmp_path):
    path = tmp_path / 'data' / 'favorites.json'
    _write(path, ['@default/photo.jpg'])
    favorites = FavoriteService(path, _library(tmp_path))

    first = favorites.read_only()
    assert first == {'@default/photo.jpg'}
    # Same object back: the point of the method is that the second caller does not
    # pay for another parse of the whole file.
    assert favorites.read_only() is first

    _write(path, ['@default/photo.jpg', '@default/second.jpg'])
    assert favorites.read_only() == {'@default/photo.jpg', '@default/second.jpg'}


def test_read_only_sees_writes_made_through_the_service(tmp_path):
    path = tmp_path / 'data' / 'favorites.json'
    favorites = FavoriteService(path, _library(tmp_path))
    favorites.set_favorite('photo.jpg', True)
    assert '@default/photo.jpg' in favorites.read_only()

    favorites.set_favorite('photo.jpg', False)
    assert favorites.read_only() == set()


def test_read_only_does_not_leak_a_mutable_cache(tmp_path):
    path = tmp_path / 'data' / 'favorites.json'
    _write(path, ['@default/photo.jpg'])
    favorites = FavoriteService(path, _library(tmp_path))
    cached = favorites.read_only()

    # Callers may hold the cached set while another request writes; the write must
    # not mutate a set someone else is iterating, and the next read must be fresh.
    favorites.set_favorite('second.jpg', True)
    assert cached == {'@default/photo.jpg'}
    assert favorites.read_only() == {'@default/photo.jpg', '@default/second.jpg'}


def test_favorites_scopes_see_a_toggle_without_a_restart(tmp_path):
    """The read-only view is only safe if a write still invalidates it.

    The favorites page and the ``scope=favorite`` listing both read the cached
    set now, so the thing worth pinning is the behaviour, not the call site: a
    favorite added through the API is on the next listing, and removing it takes
    it away again.
    """
    media = tmp_path / 'media'
    media.mkdir()
    for name in ('photo.jpg', 'second.jpg'):
        (media / name).write_bytes(b'jpeg')
    client = create_app({'TESTING': True, 'MEDIA_ROOT': media}).test_client()

    def listed():
        payload = client.get('/api/library/items?scope=favorite&mode=all&offset=0&limit=20')
        assert payload.status_code == 200
        return [item['name'] for item in payload.get_json()['data']['items']]

    assert listed() == []
    client.post('/api/favorite/photo.jpg')
    assert listed() == ['@default/photo.jpg']
    assert client.get('/favorite').status_code == 200

    client.post('/api/favorite/second.jpg')
    client.post('/api/favorite/photo.jpg')  # toggles back off
    assert listed() == ['@default/second.jpg']


def _scripts(html: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r'<script[^>]*src="[^"]*?/([^"/?]+\.js)', html)]


def test_session_guard_runs_before_every_page_controller(tmp_path):
    """The 401 wrapper must exist by the time a controller sends its first request.

    Settings fires six requests as soon as its script runs; while the wrapper was
    installed by base.js (the last script of <body>) an expired session reached
    those requests untouched, so the page settled into empty states and saving
    then wrote those empty states back.
    """
    (tmp_path / 'photo.jpg').write_bytes(b'jpeg')
    client = create_app({'TESTING': True, 'MEDIA_ROOT': tmp_path}).test_client()

    for page in ('/settings/', '/flow', '/library', '/'):
        html = client.get(page).get_data(as_text=True)
        scripts = _scripts(html)
        assert 'session_guard.js' in scripts, page
        guard_at = scripts.index('session_guard.js')
        controllers = [i for i, name in enumerate(scripts) if 'controller' in name]
        assert controllers, page
        assert guard_at < min(controllers), (page, scripts)
        # base.js is the other end of the contract: it is the last script.
        assert scripts.index('base.js') > guard_at, page


def test_session_guard_is_served_and_redirects_on_401(tmp_path):
    client = create_app({'TESTING': True, 'MEDIA_ROOT': tmp_path}).test_client()
    asset = client.get('/static/session_guard.js')

    assert asset.status_code == 200
    body = asset.get_data(as_text=True)
    assert '401' in body and '/login?next=' in body
    assert 'response.status === 403' in body  # the CSRF reload kept its behaviour


def test_base_js_no_longer_installs_a_second_wrapper(tmp_path):
    """Two wrappers would redirect twice and double every response clone."""
    client = create_app({'TESTING': True, 'MEDIA_ROOT': tmp_path}).test_client()
    body = client.get('/static/base.js').get_data(as_text=True)
    assert '__tiklocalRedirecting' not in body
