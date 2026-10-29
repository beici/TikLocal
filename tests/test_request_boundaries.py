# -*- coding: utf-8 -*-
"""Request boundaries hardened by the audit rounds.

Every assertion here locks a behaviour that used to be a 500 (or an unbounded
loop) and would otherwise regress silently: non-object JSON bodies, oversized
bodies and out-of-range pagination arguments.
"""

import json
import logging

import pytest

from tiklocal.app import create_app


@pytest.fixture()
def client(tmp_path):
    return create_app({'TESTING': True, 'MEDIA_ROOT': tmp_path}).test_client()


def test_non_object_json_body_is_a_client_error_not_a_crash(client):
    # A JSON array parses fine, so `get_json() or {}` returns a list and the
    # next payload.get() raised AttributeError (HTTP 500) on 20+ routes. Such a
    # body is now rejected as a client error: an array silently meant "replace
    # the list with nothing" and wiped the user's queue.
    response = client.post('/api/download/urls', json=['a', 'b'])
    assert response.status_code == 400
    assert response.get_json()['success'] is False
    for path in ('/api/download/urls/sync', '/api/collections'):
        assert client.post(path, json=[]).status_code < 500


def test_oversized_json_body_is_rejected_before_parsing(client):
    body = json.dumps({'lines': ['x' * 1024] * 8300})
    response = client.post('/api/download/urls', data=body, content_type='application/json')
    assert response.status_code == 413


def test_feed_pagination_arguments_are_clamped(client, tmp_path):
    for index in range(3):
        (tmp_path / f'clip{index}.mp4').write_bytes(b'x')
    assert client.get('/api/feed/mix?page=99999').get_json()['items'] == []
    assert client.get('/api/feed/mix?page=abc').status_code == 200
    assert len(client.get('/api/feed/mix?size=999').get_json()['items']) <= 200


def test_thumbnail_endpoint_answers_404_for_unknown_media(client):
    # find_existing_uri() returns None and resolve_path(None) raised inside the
    # route, so a bogus name surfaced as a 500 instead of "not found".
    assert client.post('/api/thumbnail/no-such-file.mp4', json={'time': 1}).status_code == 404


def test_thumb_fallback_is_not_cached(client):
    # A failed generation used to be cached for an hour, so the grid stayed blank
    # long after the thumbnail became generatable.
    response = client.get('/thumb', query_string={'uri': 'missing.mp4'})
    assert response.mimetype == 'image/png'
    assert 'no-store' in response.headers['Cache-Control']


def test_library_stats_counts_favorites(client, tmp_path):
    for name in ('a.jpg', 'b.jpg'):
        (tmp_path / name).write_bytes(b'\xff\xd8\xff\xe0fake-jpeg')
    assert client.post('/api/favorite/a.jpg').get_json()['favorite'] is True
    assert client.post('/api/favorite/b.jpg').get_json()['favorite'] is True
    # favorite_service.load() returns a set; the set-vs-list mismatch reported
    # "0 favorites" for every user while the field went unread.
    assert client.get('/api/library/stats').get_json()['favorites'] == 2


def test_client_error_reports_reach_the_server_log(client, caplog):
    # A controller that throws used to leave the page half-initialised and the
    # server log empty, so a bug report could only say "the page is blank".
    payload = {
        'message': 'x is undefined',
        'source': 'flow_page_controller.js',
        'line': 42,
        'stack': 'TypeError\n    at boot (flow.js:1:2)',
        'url': 'http://localhost/flow',
    }
    with caplog.at_level(logging.WARNING):
        response = client.post('/api/client-log', json=payload)
    assert response.status_code == 200
    assert response.get_json()['success'] is True
    entries = [record.getMessage() for record in caplog.records if 'client error:' in record.getMessage()]
    assert entries, 'the report must land in the log'
    line = entries[0]
    assert 'x is undefined' in line
    assert 'flow_page_controller.js:42' in line
    assert 'http://localhost/flow' in line
    # A newline in the stack must not let a crafted report forge extra log lines.
    assert '\n' not in line


def test_client_error_report_rejects_non_object_body(client):
    assert client.post('/api/client-log', json=['nope']).status_code == 400
    assert client.post('/api/client-log', data='not json', content_type='text/plain').status_code == 400

