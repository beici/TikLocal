"""The timestamp template filter must format per locale and never raise."""
from __future__ import annotations

import datetime
import re

import pytest

from tiklocal.app import create_app


@pytest.fixture()
def app(tmp_path):
    return create_app(
        {
            'TESTING': True,
            'MEDIA_ROOT': str(tmp_path / 'media'),
            'TIKLOCAL_INSTANCE': str(tmp_path / 'instance'),
        }
    )


def render(app, value, language):
    with app.test_request_context('/', headers={'Accept-Language': language}):
        return app.jinja_env.filters['timestamp_to_date'](value)


@pytest.mark.parametrize('value', [None, '', 'not-a-number', float('inf'), float('nan')])
def test_invalid_values_fall_back_to_localized_text(app, value):
    assert render(app, value, 'zh-CN') == '未知时间'
    assert render(app, value, 'en-US') == 'Unknown time'


def test_valid_timestamp_is_formatted(app):
    stamp = 1793045708
    moment = datetime.datetime.fromtimestamp(stamp)

    assert render(app, stamp, 'zh-CN') == moment.strftime('%Y-%m-%d %H:%M:%S')
    english = render(app, stamp, 'en-US')
    assert re.fullmatch(r'[A-Z][a-z]{2} \d{1,2}, \d{4} \d{2}:\d{2}:\d{2}', english), english
