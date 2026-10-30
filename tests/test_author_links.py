"""The offline half of the author sync: binding home pages from a user dump.

Douyin nicknames are not unique and are exactly what a cancelled account loses, so
the dump is matched on the numeric UID inside the export folder name. The sidecar
cannot help here: it can only answer for accounts that still exist, which are the
ones that never needed the fallback.
"""

from __future__ import annotations

import json
import logging

import pytest

from tiklocal.services import author_links, author_profiles

UID = '958392617677856'
SEC = 'MS4wLjABAAAAUfusvg2CADClubXWVAELh95ejwhJyn7lFUTYtKv4qY0'
FOLDER = f'UID{UID}_泡芙_发布作品'


@pytest.fixture(autouse=True)
def reset_cli_logging(capsys):
    """Re-bind the process-wide log handlers for every CLI test.

    ``main()`` attaches a console handler once per process and it captures
    ``sys.stderr`` at that moment; pytest swaps stderr per test, so the second CLI
    test in this module would write every record to a closed stream and the
    "--- Logging error ---" traceback would land in the captured stderr. Same
    problem and same fix as ``tests/test_tls.py``.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        if isinstance(handler, logging.FileHandler):
            handler.close()
    if hasattr(root, '_tiklocal_logging_configured'):
        delattr(root, '_tiklocal_logging_configured')
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
        if isinstance(handler, logging.FileHandler):
            handler.close()
    if hasattr(root, '_tiklocal_logging_configured'):
        delattr(root, '_tiklocal_logging_configured')


@pytest.fixture(scope='module', autouse=True)
def _instance_root(tmp_path_factory):
    """Own the instance root so nothing is written outside the repository."""
    import os
    import shutil

    root = tmp_path_factory.getbasetemp().parent / f'.tmp-inst-author-links-{os.getpid()}'
    shutil.rmtree(root, ignore_errors=True)
    previous = os.environ.get('TIKLOCAL_INSTANCE')
    os.environ['TIKLOCAL_INSTANCE'] = str(root)
    yield root
    if previous is None:
        os.environ.pop('TIKLOCAL_INSTANCE', None)
    else:
        os.environ['TIKLOCAL_INSTANCE'] = previous
    shutil.rmtree(root, ignore_errors=True)


def _row(**overrides) -> dict:
    row = {
        'uid': UID,
        'sec_uid': SEC,
        'nickname': '泡芙',
        'unique_id': 'ioioi33i_Y',
        'avatar_larger': {'url_list': ['https://p3.douyinpic.com/a.jpeg']},
    }
    row.update(overrides)
    return row


class _Source:
    def __init__(self, source_id, path):
        self.id = source_id
        self.path = str(path)


class _Tree:
    """A media root holding one export folder plus a plain directory."""

    def __init__(self, root):
        self.root = root
        self.folders = []

    def add(self, folder: str) -> '_Tree':
        (self.root / folder).mkdir(parents=True, exist_ok=True)
        (self.root / folder / 'a.jpg').write_bytes(b'x')
        self.folders.append(folder)
        return self

    def sources(self, source_id='default'):
        return [_Source(source_id, self.root)]


@pytest.fixture()
def tree(tmp_path):
    root = tmp_path / 'down'
    root.mkdir()
    made = _Tree(root)
    made.add(FOLDER)
    made.add('UID2049944108403096_周66')  # no release suffix, still an export
    made.add('notes')                     # not an export folder at all
    return made


def test_the_dump_is_matched_on_the_uid_inside_the_folder_name(tree):
    authors = author_links.local_authors(tree.sources())

    assert sorted(entry['folder'] for entry in authors) == sorted([FOLDER, 'UID2049944108403096_周66'])
    plan = author_links.plan_bindings(author_links.parse_dump(json.dumps([_row()])), authors)

    assert [entry['key'] for entry in plan['bound']] == [f'default/{FOLDER}']
    assert [entry['folder'] for entry in plan['unmatched']] == ['UID2049944108403096_周66']


def test_a_renamed_account_still_binds_because_the_uid_is_the_identity(tree):
    renamed = _row(nickname='换了个名字')

    plan = author_links.plan_bindings(
        author_links.parse_dump(json.dumps([renamed])), author_links.local_authors(tree.sources())
    )

    assert plan['bound'][0]['row']['name'] == '换了个名字'
    assert plan['bound'][0]['abandoned'] is False


def test_a_placeholder_nickname_marks_the_account_instead_of_renaming_it(tree):
    gone = _row(nickname=f'用户{UID}')

    plan = author_links.plan_bindings(
        author_links.parse_dump(json.dumps([gone])), author_links.local_authors(tree.sources())
    )

    assert [entry['key'] for entry in plan['abandoned']] == [f'default/{FOLDER}']
    summary = author_links.apply_plan(plan, download_avatars=False)
    profile = author_profiles.load_profile(f'default/{FOLDER}')

    assert summary['written'] == 1
    assert 'name' not in profile
    assert profile['account_missing'] is True
    assert profile['profile_url'] == f'https://www.douyin.com/user/{SEC}'
    # A gone account's picture in the dump is Douyin's grey stand-in; taking it
    # would hide the newest-work cover the page falls back to.
    assert 'avatar_url' not in profile
    assert 'avatar_cached' not in profile


def test_a_live_account_still_takes_the_picture_from_the_dump(tree):
    plan = author_links.plan_bindings(
        author_links.parse_dump(json.dumps([_row()])), author_links.local_authors(tree.sources())
    )

    author_links.apply_plan(plan, download_avatars=False)
    profile = author_profiles.load_profile(f'default/{FOLDER}')

    assert profile['avatar_url'] == 'https://p3.douyinpic.com/a.jpeg'


def test_a_name_that_is_just_the_numeric_id_counts_as_gone(tree):
    assert author_links.looks_abandoned({'name': '89556816950', 'douyin_id': '89556816950'}) is True
    assert author_links.looks_abandoned({'name': '泡芙', 'douyin_id': '89556816950'}) is False
    assert author_links.looks_abandoned({'name': '用户12345678_粉丝号'}) is False


def test_binding_stores_the_link_and_leaves_a_good_name_alone(tree):
    author_profiles.save_profile(f'default/{FOLDER}', {'name': '已经同步过的名字'})

    plan = author_links.plan_bindings(
        author_links.parse_dump(json.dumps([_row()])), author_links.local_authors(tree.sources())
    )
    author_links.apply_plan(plan, download_avatars=False)
    profile = author_profiles.load_profile(f'default/{FOLDER}')

    assert profile['name'] == '已经同步过的名字'
    assert profile['douyin_id'] == 'ioioi33i_Y'
    assert profile['uid'] == UID
    assert profile['account_missing'] is False


def test_a_live_sync_clears_the_missing_note():
    key = f'default/{FOLDER}'
    author_profiles.save_profile(key, {'account_missing': True})

    author_profiles.store_author_from_payload(key, {'author': _row()}, fetch_avatar=False)

    assert author_profiles.load_profile(key).get('account_missing') is False


def test_the_same_author_in_two_roots_gets_one_row_per_root(tree, tmp_path):
    other = _Tree(tmp_path / 'second')
    other.root.mkdir()
    other.add(FOLDER)

    authors = author_links.local_authors(tree.sources('one') + other.sources('two'))
    keys = [entry['key'] for entry in authors]

    # The same folder name under two sources is two different authors: the key
    # carries the source, so binding one never overwrites the other.
    assert f'one/{FOLDER}' in keys
    assert f'two/{FOLDER}' in keys
    assert len(keys) == len(set(keys))


def test_a_link_only_dump_binds_nothing_because_it_carries_no_uid(tree):
    text = f'https://www.douyin.com/user/{SEC}\nhttps://www.douyin.com/user/MS4wLjABAAAAother\n'

    rows = author_links.parse_dump(text)
    plan = author_links.plan_bindings(rows, author_links.local_authors(tree.sources()))

    assert len(rows) == 2
    assert plan['bound'] == []
    # A link proves nothing about which export folder it belongs to: the UID is
    # the only field both sides hold, and this dump does not carry one.
    assert [entry['key'] for entry in plan['unmatched']] == ['default/UID2049944108403096_周66',
                                                            f'default/{FOLDER}']


def test_a_jsonl_dump_and_a_csv_line_and_a_noise_line_all_read():
    text = '\n'.join([
        '# a comment',
        json.dumps(_row()),
        f'{UID},{SEC},泡芙',
        'not a record',
        '',
    ])

    rows = author_links.parse_dump(text)

    assert [row['uid'] for row in rows] == [UID, UID]
    assert rows[1]['sec_uid'] == SEC
    assert rows[1]['name'] == '泡芙'


def test_a_dump_with_a_top_level_list_key_is_read_too():
    text = json.dumps({'code': 0, 'data': [_row(), _row(uid='1', sec_uid='MS4wLjABAAAAa')]})

    rows = author_links.parse_dump(text)

    assert len(rows) == 2


def test_an_entry_without_a_profile_id_is_reported_not_guessed(tree):
    broken = _row(sec_uid='', sec_user_id='')

    plan = author_links.plan_bindings(
        author_links.parse_dump(json.dumps([broken])), author_links.local_authors(tree.sources())
    )

    # The dump row has no sec_uid, so the row itself is unusable and never reaches
    # the plan as a binding.
    assert plan['bound'] == []
    assert plan['abandoned'] == []
    assert plan['unusable'] == [] or plan['unmatched']


def _run_cli(monkeypatch, argv):
    import sys

    from tiklocal.run import main

    monkeypatch.setattr('tiklocal.run.load_config', lambda: {})
    monkeypatch.setattr(sys, 'argv', ['tiklocal', *argv])
    return main


def test_the_command_binds_and_reports_what_it_did(tree, tmp_path, monkeypatch, capsys):
    dump = tmp_path / 'resolved_users.jsonl'
    dump.write_text(json.dumps(_row()) + '\n', encoding='utf-8')

    _run_cli(monkeypatch, ['author', 'import-links', str(dump), '--no-avatar',
                           '--media-source', f'default={tree.root}'])()

    out = capsys.readouterr().out
    assert 'Bound: 1' in out
    assert 'No entry in the dump: 1' in out
    assert author_profiles.load_profile(f'default/{FOLDER}')['profile_url'].endswith(SEC)


def test_the_report_counts_the_accounts_that_look_gone(tree, tmp_path, monkeypatch, capsys):
    dump = tmp_path / 'dump.jsonl'
    dump.write_text(json.dumps(_row(nickname=f'用户{UID}')), encoding='utf-8')

    _run_cli(monkeypatch, ['author', 'import-links', str(dump), '--no-avatar',
                           '--media-source', f'default={tree.root}'])()

    assert 'Bound: 1 (1 look like a cancelled or blocked account)' in capsys.readouterr().out


def test_a_dry_run_writes_nothing(tree, tmp_path, monkeypatch, capsys):
    dump = tmp_path / 'dump.jsonl'
    dump.write_text(json.dumps(_row()), encoding='utf-8')

    _run_cli(monkeypatch, ['author', 'import-links', str(dump), '--dry-run',
                           '--media-source', f'default={tree.root}'])()

    assert 'Would bind: 1' in capsys.readouterr().out
    assert author_profiles.load_profile(f'default/{FOLDER}') == {}


def test_an_empty_dump_stops_with_one_readable_line(tree, tmp_path, monkeypatch, capsys):
    dump = tmp_path / 'empty.txt'
    dump.write_text('# nothing here\n', encoding='utf-8')

    with pytest.raises(SystemExit) as exit_info:
        _run_cli(monkeypatch, ['author', 'import-links', str(dump),
                               '--media-source', f'default={tree.root}'])()

    assert exit_info.value.code == 1
    err = capsys.readouterr().err
    assert 'No usable records' in err
    assert 'Traceback' not in err


def test_a_missing_dump_file_names_the_file(tree, tmp_path, monkeypatch, capsys):
    missing = tmp_path / 'nope.jsonl'

    with pytest.raises(SystemExit) as exit_info:
        _run_cli(monkeypatch, ['author', 'import-links', str(missing),
                               '--media-source', f'default={tree.root}'])()

    assert exit_info.value.code == 1
    err = capsys.readouterr().err
    assert 'Cannot read the dump' in err
    assert 'Traceback' not in err


def test_the_command_reads_standard_input_when_no_file_is_named(tree, monkeypatch, capsys):
    import io
    import sys

    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(_row())))

    _run_cli(monkeypatch, ['author', 'import-links', '--no-avatar',
                           '--media-source', f'default={tree.root}'])()

    assert 'Records in the dump: 1' in capsys.readouterr().out


# --- the settings card, over HTTP ------------------------------------------


@pytest.fixture()
def client(tree):
    from tiklocal.app import create_app

    app = create_app({
        'TESTING': True,
        'MEDIA_ROOT': tree.root,
        'MEDIA_SOURCES': [{'id': 'default', 'name': 'down', 'path': str(tree.root)}],
        'TIKTOK_DOWNLOADER': {'base_url': ''},
    })
    return app.test_client()


def _post_links(client, path):
    return client.post('/api/author/links', json={'path': str(path), 'no_avatar': True})


def test_the_card_binds_from_the_folder_the_reader_knows(client, tree, tmp_path):
    # The reader knows the ``url`` folder; the uid-carrying dump is written to the
    # sibling ``Volume`` folder, so pointing at the folder has to find it.
    checkout = tmp_path / 'TikTokDownloader-master'
    (checkout / 'url').mkdir(parents=True)
    (checkout / 'url' / 'user-url.txt').write_text(
        f'https://www.douyin.com/user/{SEC}\n', encoding='utf-8')
    (checkout / 'Volume').mkdir()
    (checkout / 'Volume' / 'resolved_users.jsonl').write_text(
        json.dumps(_row()) + '\n', encoding='utf-8')

    response = _post_links(client, checkout / 'url')
    payload = response.get_json()

    assert response.status_code == 200
    assert payload['bound'] == 1
    assert payload['path'].endswith('resolved_users.jsonl')
    assert author_profiles.load_profile(f'default/{FOLDER}')['sec_uid'] == SEC


def test_the_card_says_when_the_list_has_no_uid_to_match_on(client, tree, tmp_path):
    dump = tmp_path / 'user-url.txt'
    dump.write_text(f'https://www.douyin.com/user/{SEC}\n', encoding='utf-8')

    payload = _post_links(client, dump).get_json()

    assert payload['reason'] == 'links_only'
    assert payload['bound'] == 0
    assert author_profiles.load_profile(f'default/{FOLDER}') == {}


def test_the_card_names_a_path_it_cannot_see(client, tmp_path):
    response = _post_links(client, tmp_path / 'nope')

    assert response.status_code == 400
    assert response.get_json()['reason'] == 'missing'


def test_the_card_refuses_an_empty_ask(client):
    response = client.post('/api/author/links', json={'path': '  '})

    assert response.status_code == 400
    assert response.get_json()['ok'] is False


def test_the_card_remembers_the_file_it_read(client, tree, tmp_path):
    dump = tmp_path / 'resolved_users.jsonl'
    dump.write_text(json.dumps(_row()) + '\n', encoding='utf-8')

    _post_links(client, dump)

    remembered = client.get('/api/author/links').get_json()
    assert remembered['path'] == str(dump)

