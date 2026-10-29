"""Round 36: first-run and empty-state regressions.

The three cases cover failures that only showed up on a cold install: a named
source silently gaining a duplicate `default` folder, a trash directory that can
be stat-ed but not listed, and a file whose name starts with a space being
dropped from the index for good.
"""
from pathlib import Path
from types import SimpleNamespace

import tiklocal.app as app_module
from tiklocal.app import create_app
from tiklocal.services.library import build_media_sources
from tiklocal.services.trash import TrashService


def _source(source_id: str, path: Path) -> dict:
    return {'id': source_id, 'name': source_id.title(), 'path': str(path)}


def test_named_sources_do_not_gain_a_duplicate_default_folder(tmp_path, monkeypatch):
    media = tmp_path / 'photos'
    media.mkdir()
    seen = {}

    def spy(root, sources=None):
        seen['root'] = root
        seen['sources'] = sources
        return build_media_sources(root, sources)

    monkeypatch.setattr(app_module, 'build_media_sources', spy)
    create_app({
        'TESTING': True,
        'MEDIA_ROOT': media,
        'MEDIA_SOURCES': [_source('photos', media)],
        'MEDIA_ROOT_DERIVED_FROM_SOURCES': True,
    })

    # The folder argument has to be dropped when named sources exist, otherwise
    # build_media_sources() appends a second 'default' source on the same folder.
    assert seen['root'] is None
    assert [source.id for source in build_media_sources(seen['root'], seen['sources'])] == ['photos']


def test_a_trash_directory_that_cannot_be_listed_reports_no_entries(tmp_path, monkeypatch):
    media = tmp_path / 'photos'
    trash_root = media / '.tiklocal-trash'
    trash_root.mkdir(parents=True)
    service = TrashService(SimpleNamespace(sources=[SimpleNamespace(path=media)]))

    real_iterdir = Path.iterdir

    def refuse(self):
        if self == trash_root:
            raise OSError(13, 'Permission denied', str(self))
        return real_iterdir(self)

    monkeypatch.setattr(Path, 'iterdir', refuse)
    assert service.entries() == []


def test_a_file_with_a_leading_space_is_indexed(tmp_path):
    media = tmp_path / 'media'
    media.mkdir()
    (media / ' leading.mp4').write_bytes(b'video')

    app = create_app({'TESTING': True, 'MEDIA_ROOT': media})
    payload = app.test_client().get('/api/library/items?scope=all&mode=all&limit=10').get_json()
    names = [item['name'] for item in payload['data']['items']]
    assert any(name.endswith(' leading.mp4') for name in names), names
