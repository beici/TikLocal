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


def test_js_catalog_is_a_nav_and_common_subset():
    catalog = js_catalog('en')
    assert catalog['nav.home'] == 'Home'
    assert 'settings.section.about' not in catalog
    assert js_catalog('zh')['nav.home'] == '首页'
