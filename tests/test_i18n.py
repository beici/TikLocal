"""Unit tests for the lightweight zh/en i18n helper."""
import pytest

from tiklocal.i18n import (
    TRANSLATIONS,
    detect_locale,
    js_catalog,
    normalize_locale,
    translate,
)


def _cookies(mapping):
    return mapping.get


def test_translation_tables_are_aligned():
    for locale, table in TRANSLATIONS.items():
        assert table, locale
    assert set(TRANSLATIONS['zh']) == set(TRANSLATIONS['en'])


@pytest.mark.parametrize('raw,expected', [
    (None, 'zh'),
    ('', 'zh'),
    ('zh-CN', 'zh'),
    ('en-US,en;q=0.9', 'en'),
    ('EN', 'en'),
    ('fr-FR', 'zh'),
])
def test_normalize_locale(raw, expected):
    assert normalize_locale(raw) == expected


def test_detect_locale_prefers_cookie_then_accept_language():
    assert detect_locale(_cookies({'lang': 'en'}), 'zh-CN,zh;q=0.9') == 'en'
    assert detect_locale(_cookies({}), 'en-GB,en;q=0.9,zh;q=0.5') == 'en'
    assert detect_locale(_cookies({}), 'ja;q=0.8,en;q=0.2') == 'en'
    assert detect_locale(_cookies({}), 'zh-CN,zh;q=0.9') == 'zh'
    assert detect_locale(_cookies({}), None) == 'zh'


def test_translate_falls_back_to_key_and_formats_params():
    assert translate('zh', 'nav.saved') == '收藏'
    assert translate('en', 'nav.saved') == 'Saved'
    assert translate('en', 'no.such.key') == 'no.such.key'
    assert translate('en', 'missing.key', name='x') == 'missing.key'
    TRANSLATIONS['en']['test.template'] = 'Hello {name}!'
    try:
        assert translate('en', 'test.template', name='A') == 'Hello A!'
    finally:
        del TRANSLATIONS['en']['test.template']


def test_js_catalog_covers_every_key():
    catalog = js_catalog('en')
    assert catalog['nav.home'] == 'Home'
    assert catalog['settings.section.about'] == 'About'
    assert set(catalog) == set(TRANSLATIONS['en'])
    assert js_catalog('zh')['nav.home'] == '首页'
    assert js_catalog('en') is not TRANSLATIONS['en']


def test_page_catalogs_are_merged_into_the_main_table():
    from tiklocal import i18n_pages_flow, i18n_pages_library, i18n_pages_media, i18n_pages_radio

    for module in (i18n_pages_flow, i18n_pages_library, i18n_pages_media, i18n_pages_radio):
        for locale in ('zh', 'en'):
            assert set(module.TRANSLATIONS[locale]) == set(module.TRANSLATIONS['zh']), module.__name__
            for key in module.TRANSLATIONS[locale]:
                assert key in TRANSLATIONS[locale], (module.__name__, key)


def test_merge_rejects_duplicate_keys():
    from tiklocal import i18n_pages_flow
    from tiklocal.i18n import _merge_page_catalogs

    # A key already owned by the main table must not be shadowed by a page
    # catalog: the merge raises instead of silently overwriting it.
    i18n_pages_flow.TRANSLATIONS['zh']['nav.home'] = '重复'
    try:
        with pytest.raises(ValueError):
            _merge_page_catalogs()
    finally:
        del i18n_pages_flow.TRANSLATIONS['zh']['nav.home']


def test_templates_and_scripts_only_use_known_keys():
    # The catalog tests above only compare catalog to catalog. This one catches a
    # template/JS key that was renamed on one side only: translate() would render
    # the raw key into the page while every other test stayed green.
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / 'tiklocal'
    files = list((root / 'templates').rglob('*.html'))
    files += [p for p in (root / 'static').glob('*.js') if '.min.' not in p.name]
    # Python call sites matter too: the radio device API used to call
    # t('radio.error.audio_not_found'), a key that never existed, and every
    # response shipped the raw key because only templates/JS were scanned.
    files += list(root.rglob('*.py'))
    pattern = re.compile(r"""\b(?:t|T)\(\s*['"]([A-Za-z][\w.]*)['"]""")
    used = set()
    for path in files:
        used.update(pattern.findall(path.read_text(encoding='utf-8')))
    missing = sorted(used - set(TRANSLATIONS['zh']))
    assert not missing, missing


def test_t_follows_the_active_request_locale():
    from flask import Flask

    from tiklocal.i18n import t

    assert t('nav.home') == '首页'
    app = Flask(__name__)
    with app.test_request_context(headers={'Accept-Language': 'en-US,en;q=0.9'}):
        assert t('nav.home') == 'Home'
    with app.test_request_context(headers={'Cookie': 'lang=en'}):
        assert t('nav.home') == 'Home'
    with app.test_request_context(headers={'Cookie': 'lang=zh'}):
        assert t('nav.home') == '首页'
