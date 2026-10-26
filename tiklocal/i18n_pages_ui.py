"""Locale catalog for small shared UI affordances that no single page owns.

Kept in its own module so parallel work on a page catalog never has to touch
this file, and so a generic control (a "show all" toggle, a filter chip) can be
reused by several pages without duplicating keys.
"""

TRANSLATIONS = {
    'zh': {
        'download.urls.expand': '展开全部（{count} 条）',
        'download.urls.collapse': '收起',
    },
    'en': {
        'download.urls.expand': 'Show all ({count})',
        'download.urls.collapse': 'Show less',
    },
}
