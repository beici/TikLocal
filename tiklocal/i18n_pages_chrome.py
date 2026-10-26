"""Locale catalog for the Douyin-style Flow chrome and the topic (hashtag) feed.

Kept apart from ``i18n_pages_flow.py`` so the Flow chrome and the author page
can be translated in parallel without two writers touching one file.
"""

TRANSLATIONS = {
    'zh': {
        'flow.topbar.recommend': '推荐',
        'flow.tag.back': '返回',
        'flow.tag.count': '{count} 个作品',
        'flow.tag.empty': '这个话题下还没有作品。',
        'flow.nav.me': '我的',
        'flow.avatar.alt': '作者头像',
    },
    'en': {
        'flow.topbar.recommend': 'For You',
        'flow.tag.back': 'Back',
        'flow.tag.count': '{count} videos',
        'flow.tag.empty': 'No videos under this topic yet.',
        'flow.nav.me': 'Me',
        'flow.avatar.alt': 'Author avatar',
    },
}
