"""Regressions for the round-17 findings (templates, CSS and settings wiring)."""

from pathlib import Path

from tiklocal.app import create_app

STATIC = Path(__file__).resolve().parents[1] / 'tiklocal' / 'static'


def _client(tmp_path):
    (tmp_path / 'a.jpg').write_bytes(b'\xff\xd8\xff\xd9')
    return create_app({'TESTING': True, 'MEDIA_ROOT': tmp_path}).test_client()


def test_every_page_gets_its_own_title_and_a_skip_link(tmp_path):
    """One shared <title> made tabs and history indistinguishable, and the
    keyboard path to the content started after twelve dock links."""
    client = _client(tmp_path)
    titles = {}
    for url in ('/', '/library', '/download', '/settings/', '/nope'):
        html = client.get(url).get_data(as_text=True)
        titles[url] = html.split('<title>')[1].split('</title>')[0]
        assert 'class="skip-link" href="#main"' in html, url
        assert 'id="main"' in html, url
    assert len(set(titles.values())) == len(titles), titles
    assert titles['/nope'].startswith('404')


def test_error_page_uses_a_body_text_colour_not_the_dock_icon_colour(tmp_path):
    """``--dock-ink`` is a 3:1 icon colour; on the error page it was body text
    at 3.09:1 against the light background."""
    html = _client(tmp_path).get('/no-such-page').get_data(as_text=True)
    assert 'text-gray-600 dark:text-gray-300' in html
    assert '--dock-ink' not in html


def test_print_stylesheet_hides_the_fixed_chrome():
    css = (STATIC / 'base.css').read_text(encoding='utf-8')
    assert '@media print' in css
    block = css.split('@media print', 1)[1]
    assert '.floating-dock' in block
    assert '.mobile-app-header' in block
    assert 'background: #fff !important' in block


def test_service_worker_retirement_script_is_cache_busted(tmp_path):
    """Every other asset carries ?v=; the script that retires a stale service
    worker was the one asset that could keep serving an old copy."""
    html = _client(tmp_path).get('/library').get_data(as_text=True)
    assert '/static/retire_pwa.js?v=' in html


def test_settings_concurrency_buttons_render_one_pressed_state(tmp_path):
    html = _client(tmp_path).get('/settings/').get_data(as_text=True)
    assert 'id="download-levels-copy"' in html
    for level in ('1', '2', '4'):
        assert f'data-concurrency="{level}"' in html


def test_adding_urls_reports_what_it_dropped(tmp_path):
    """The page used to say "imported 8" for five new URLs: junk and the
    MAX_LINES overflow were both dropped without a word."""
    from tiklocal.services.url_list import MAX_LINES, UrlListStore

    store = UrlListStore(tmp_path / 'urls.txt')
    store.replace(['https://example.com/1.mp4'])
    _, added, rejected, truncated = store.add_reporting([
        'https://example.com/1.mp4',      # already there
        'https://example.com/2.mp4',      # new
        'ftp://example.com/3.mp4',        # unsupported scheme
        'not a url',                      # junk
        '',                               # blank
    ])
    assert (added, rejected, truncated) == (1, 3, 0)

    many = [f'https://example.com/fill-{index}.mp4' for index in range(MAX_LINES + 2)]
    _, added, rejected, truncated = UrlListStore(tmp_path / 'big.txt').add_reporting(many)
    assert added == MAX_LINES
    assert rejected == 0
    assert truncated == 2


def test_url_api_returns_import_counts(tmp_path):
    client = _client(tmp_path)
    response = client.post('/api/download/urls', json={
        'action': 'add',
        'text': 'https://example.com/ok.mp4\nftp://example.com/nope.mp4\n',
    })
    assert response.status_code == 200
    data = response.get_json()['data']
    assert data['added'] == 1
    assert data['rejected'] == 1
    assert data['truncated'] == 0
    assert data['lines'] == ['https://example.com/ok.mp4']
