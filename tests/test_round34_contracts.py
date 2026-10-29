"""Contracts that survive a restart or a downgrade.

Round 34 looked at what each layer promises the others: HTTP envelopes, i18n keys,
stored JSON shapes and command-line entry points. The regressions pinned here all
share one shape — a field that one side writes and the other side quietly forgets,
so the feature looks fine until the process restarts.
"""

import json
from types import SimpleNamespace

from tiklocal import run as run_module
from tiklocal.services.downloader import (
    DownloadConfigStore,
    DownloadHistoryStore,
    DownloadManager,
    DownloadSourceStore,
)


def make_manager(tmp_path):
    return DownloadManager(
        tmp_path / 'media',
        DownloadConfigStore(tmp_path / 'download_config.json'),
        DownloadHistoryStore(tmp_path / 'download_jobs.json'),
        DownloadSourceStore(tmp_path / 'download_sources.json'),
    )


def test_a_retried_job_stays_retried_after_a_restart(tmp_path):
    """jobs.json is rewritten from the in-memory job dict.

    `_load_history` used to rebuild each job from a fixed whitelist, so any field
    missing from it was erased from disk on the next save. Dropping `retried_by`
    made the download page render the retry button again after a restart, and the
    guard in `retry_job` no longer matched: the same URL was downloaded twice.
    `locale` went the same way, which reset a job's messages to the default
    language.
    """
    (tmp_path / 'download_jobs.json').write_text(json.dumps([{
        'id': 'old',
        'url': 'https://example.com/a',
        'status': 'failed',
        'created_at': '2026-01-01T00:00:00Z',
        'finished_at': '2026-01-01T00:01:00Z',
        'retried_by': 'new',
        'locale': 'zh',
        'partial': True,
    }]), encoding='utf-8')

    manager = make_manager(tmp_path)
    job = manager.get_job('old')

    assert job is not None
    assert job['retried_by'] == 'new'
    assert job['locale'] == 'zh'
    assert job['partial'] is True
