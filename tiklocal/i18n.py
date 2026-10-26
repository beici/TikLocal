"""Lightweight zh/en translations for templates and client scripts.

Locale resolution order: ``lang`` cookie (manual selection, per device) >
``Accept-Language`` header > Chinese default. The settings page writes both a
cookie and localStorage (``tiklocal:lang``) and reloads, so server-rendered
templates and client scripts always agree.
"""
from __future__ import annotations

import re

SUPPORTED_LOCALES = ('zh', 'en')
DEFAULT_LOCALE = 'zh'
LOCALE_COOKIE = 'lang'
_LANG_RE = re.compile(r'([a-zA-Z]{1,8})(?:-[a-zA-Z0-9]{1,8})*(?:;q=([0-9.]+))?')

# Key catalog, grouped by file domain so parallel edits stay mergeable.
# `zh` is the source of truth; `en` must define every key (tests enforce this).
TRANSLATIONS: dict[str, dict[str, str]] = {
    'zh': {
        # ---- common (base.html, shared JS) ----
        'common.app_name': 'tikLocal',
        'common.unknown_time': '未知时间',
        'common.cancel': '取消',
        'common.confirm': '确认',
        'common.close': '关闭',
        'common.delete': '删除',
        'common.save': '保存',
        'common.loading': '加载中…',
        'common.retry': '重试',
        'common.all': '全部',
        'common.search': '搜索',
        'common.open_settings': '打开设置',
        # ---- navigation ----
        'nav.home': '首页',
        'nav.flow': '去刷',
        'nav.radio': '电台',
        'nav.library': '媒体库',
        'nav.saved': '收藏',
        'nav.download': '下载',
        'nav.settings': '设置',
        'nav.sign_out': '退出登录',
        'nav.open_menu': '打开媒体与工具菜单',
        'nav.return_home': '返回首页',
        'nav.experience_mode': '体验模式',
        'nav.app_navigation': '应用导航',
        'nav.main_navigation': '主导航',
        'nav.media_and_tools': '媒体与工具',
        'nav.tiklocal_home': 'TikLocal 首页',
        'nav.sign_out_label': '退出登录',
        # ---- page titles ----
        'title.download': '媒体导入',
        'title.saved': '收藏',
        'title.settings': '设置',
        'title.login': '登录',
        # ---- settings page ----
        'settings.section.appearance': '外观',
        'settings.section.radio_clients': '电台客户端',
        'settings.section.library': '媒体库',
        'settings.section.trash': '最近删除',
        'settings.section.privacy': '隐私',
        'settings.section.downloads': '下载',
        'settings.section.about': '关于',
        'settings.theme.light': '浅色',
        'settings.theme.dark': '深色',
        'settings.theme.system': '跟随系统',
        'settings.douyin.title': '抖音短视频模式',
        'settings.douyin.copy': '以抖音风格的导航、作者与文案浮层展示 Flow 信息流。',
        'settings.language.title': '语言 / Language',
        'settings.language.copy': '界面显示语言，选择后立即生效。',
        'settings.language.zh': '中文',
        'settings.language.en': 'English',
        'settings.language.changed': '语言已切换',
    },
    'en': {
        # ---- common (base.html, shared JS) ----
        'common.app_name': 'tikLocal',
        'common.unknown_time': 'Unknown time',
        'common.cancel': 'Cancel',
        'common.confirm': 'Confirm',
        'common.close': 'Close',
        'common.delete': 'Delete',
        'common.save': 'Save',
        'common.loading': 'Loading…',
        'common.retry': 'Retry',
        'common.all': 'All',
        'common.search': 'Search',
        'common.open_settings': 'Open Settings',
        # ---- navigation ----
        'nav.home': 'Home',
        'nav.flow': 'Flow',
        'nav.radio': 'Radio',
        'nav.library': 'Library',
        'nav.saved': 'Saved',
        'nav.download': 'Download',
        'nav.settings': 'Settings',
        'nav.sign_out': 'Sign Out',
        'nav.open_menu': 'Open media and tools menu',
        'nav.return_home': 'Return home',
        'nav.experience_mode': 'Experience mode',
        'nav.app_navigation': 'App navigation',
        'nav.main_navigation': 'Main navigation',
        'nav.media_and_tools': 'Media and tools',
        'nav.tiklocal_home': 'TikLocal home',
        'nav.sign_out_label': 'Sign out',
        # ---- page titles ----
        'title.download': 'Media Import',
        'title.saved': 'Saved',
        'title.settings': 'Settings',
        'title.login': 'Sign in',
        # ---- settings page ----
        'settings.section.appearance': 'Appearance',
        'settings.section.radio_clients': 'Radio Clients',
        'settings.section.library': 'Media Library',
        'settings.section.trash': 'Recently Deleted',
        'settings.section.privacy': 'Privacy',
        'settings.section.downloads': 'Downloads',
        'settings.section.about': 'About',
        'settings.theme.light': 'Light',
        'settings.theme.dark': 'Dark',
        'settings.theme.system': 'System',
        'settings.douyin.title': 'Douyin Feed Style',
        'settings.douyin.copy': 'Show the Flow feed with Douyin-style navigation, author, and caption overlays.',
        'settings.language.title': '语言 / Language',
        'settings.language.copy': 'Interface display language; applies immediately after choosing.',
        'settings.language.zh': '中文',
        'settings.language.en': 'English',
        'settings.language.changed': 'Language updated',
    },
}

_JS_REVEAL = ('window.__TIKLOCAL_I18N__ = ', ';\n')


def normalize_locale(value: str | None) -> str:
    """Map an arbitrary language tag to a supported locale."""
    if not value:
        return DEFAULT_LOCALE
    tag = value.strip().lower()
    if tag.startswith(SUPPORTED_LOCALES[0]):
        return SUPPORTED_LOCALES[0]
    if tag.startswith(SUPPORTED_LOCALES[1]):
        return SUPPORTED_LOCALES[1]
    return DEFAULT_LOCALE


def detect_locale(get_cookie, accept_language: str | None) -> str:
    """Cookie choice wins, then Accept-Language quality order, then default."""
    cookie_value = get_cookie(LOCALE_COOKIE)
    if cookie_value:
        return normalize_locale(cookie_value)
    candidates: list[tuple[float, str, bool]] = []
    for match in _LANG_RE.finditer(accept_language or ''):
        tag, quality = match.group(1), match.group(2)
        try:
            q = float(quality) if quality is not None else 1.0
        except ValueError:
            q = 0.0
        prefix = tag.strip().lower()
        explicit = prefix.startswith(SUPPORTED_LOCALES) and any(
            prefix == locale or prefix.startswith(locale + '-')
            for locale in SUPPORTED_LOCALES
        )
        candidates.append((q, normalize_locale(tag), explicit))
    # Prefer the highest-quality tag the user explicitly asked for; an
    # unsupported language (ja, fr, …) falls through to the default.
    for _, locale, explicit in sorted(candidates, key=lambda row: -row[0]):
        if explicit:
            return locale
    return DEFAULT_LOCALE


def translate(locale: str, key: str, **params: object) -> str:
    table = TRANSLATIONS.get(locale) or TRANSLATIONS[DEFAULT_LOCALE]
    text = table.get(key)
    if text is None:
        text = TRANSLATIONS[DEFAULT_LOCALE].get(key, key)
    if params:
        try:
            return text.format(**params)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def js_catalog(locale: str) -> dict[str, str]:
    """Strings needed by client scripts; keep the payload small."""
    prefix = ('nav.', 'common.')
    table = TRANSLATIONS.get(locale) or TRANSLATIONS[DEFAULT_LOCALE]
    return {key: text for key, text in table.items() if key.startswith(prefix)}


def js_catalog_script(locale: str) -> str:
    import json

    return _JS_REVEAL[0] + json.dumps(js_catalog(locale), ensure_ascii=False) + _JS_REVEAL[1]
