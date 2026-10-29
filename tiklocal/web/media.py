"""Media details, file delivery, deletion and thumbnail selection."""

import datetime
import errno
import io
import math
import mimetypes
import time
import zlib
from email.utils import formatdate
from pathlib import Path
from urllib.parse import quote

from flask import Response, redirect, render_template, request, send_file

from tiklocal.i18n import t
from tiklocal.services.embedded_metadata import read_embedded_generation
from tiklocal.services.library import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from tiklocal.services.media_info import probe_media_duration
from tiklocal.web import json_body
from tiklocal.web.media_payloads import media_urls

# Inline delivery is limited to formats the app itself renders; anything else in
# the media library (svg, html, js) is downloaded as an opaque attachment.
INLINE_MEDIA_SUFFIXES = frozenset(VIDEO_EXTENSIONS | IMAGE_EXTENSIONS | AUDIO_EXTENSIONS)


def format_size(num_bytes) -> str:
    """Human file size, deliberately mirroring the template's filesizeformat filter."""
    try:
        size = float(num_bytes)
    except (TypeError, ValueError):
        return '0 B'
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if size < 1024.0 or unit == 'TB':
            return f'{int(size)} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024.0
    return '0 B'


def format_duration(seconds) -> str:
    """``m:ss`` or ``h:mm:ss``; '' when the length is unknown."""
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return ''
    if total <= 0:
        return ''
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f'{hours}:{minutes:02d}:{secs:02d}'
    return f'{minutes}:{secs:02d}'



def _byte_range(header: str | None, size: int):
    """Classify a ``Range`` header against a file of ``size`` bytes.

    Returns ``(start, end)`` for a satisfiable single range, ``'ignore'`` when the
    header is unsupported (multi-range, bad syntax) and ``'unsatisfiable'`` when it
    parses but points past the end. Werkzeug answers every non-single-range request
    with 416, which makes a legal multi-range request look like a missing file.
    """
    if not header or not header.startswith('bytes='):
        return 'ignore'
    if size <= 0:
        # RFC 9110: no byte range of an empty representation is satisfiable.
        return 'unsatisfiable'
    spec = header[len('bytes='):].strip()
    if ',' in spec or '-' not in spec:
        return 'ignore'
    first, _, last = spec.partition('-')
    try:
        if not first:
            length = int(last)
            if length <= 0:
                return 'ignore'
            return max(size - length, 0), size - 1
        start = int(first)
        end = int(last) if last else size - 1
    except ValueError:
        return 'ignore'
    if start >= size:
        return 'unsatisfiable'
    if start > end:
        return 'ignore'
    return start, min(end, size - 1)


class _BoundedReader:
    """A file slice that stops after ``length`` bytes.

    Both ``wsgi.file_wrapper`` (waitress) and Werkzeug's ``FileWrapper`` pull from a
    file-like object until EOF, so handing them the raw file object would ship the
    rest of the file as the body of a 206 response.
    """

    def __init__(self, handle, start: int, length: int):
        self._handle = handle
        self._remaining = max(0, int(length))
        handle.seek(start)

    def read(self, size: int = -1) -> bytes:
        if self._remaining <= 0:
            return b''
        if size is None or size < 0:
            size = self._remaining
        chunk = self._handle.read(min(size, self._remaining))
        self._remaining -= len(chunk)
        return chunk

    def close(self) -> None:
        try:
            self._handle.close()
        except OSError:
            pass


def _iter_reader(reader, block_size: int = 64 * 1024):
    """Drive a bounded reader as a WSGI app_iter (used without file_wrapper)."""
    try:
        while True:
            chunk = reader.read(block_size)
            if not chunk:
                return
            yield chunk
    finally:
        reader.close()



def register_media_routes(app, library_service, media_index, thumbnail_service, download_manager, trash_service, library_indexer,
                          activity_store, favorite_service=None, collection_store=None, image_metadata=None):
    def neighbors(media_type, name):
        """Previous and next items in library order (newest first)."""
        return media_index.neighbors(media_type, name)

    @app.route('/detail/<path:name>')
    def detail_view(name):
        name = library_service.find_existing_uri(name)
        target = library_service.resolve_path(name)
        if not target or not target.exists():
            return render_template('error.html', status=404,
                                   message=t('detail.error.file_not_found')), 404

        if target.suffix.lower() in IMAGE_EXTENSIONS:
            return redirect(f"/image?uri={quote(name)}")
        source_meta = download_manager.resolve_source_for_file(name)
        file_path_encoded = quote(name, safe='/')
        file_query_encoded = quote(name, safe='')

        prev_item, next_item = neighbors('video', name)
        prev_item_path_encoded = quote(prev_item, safe='/') if prev_item else None
        next_item_path_encoded = quote(next_item, safe='/') if next_item else None

        canonical = library_service.canonicalize_uri(name)
        stat = target.stat()
        return render_template(
            'detail.html',
            file=name,
            is_video=target.suffix.lower() in VIDEO_EXTENSIONS,
            resume=activity_store.positions_for([canonical]).get(canonical),
            file_path_encoded=file_path_encoded,
            file_query_encoded=file_query_encoded,
            mtime=datetime.datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M'),
            size=stat.st_size,
            previous_item=prev_item,
            next_item=next_item,
            previous_item_path_encoded=prev_item_path_encoded,
            next_item_path_encoded=next_item_path_encoded,
            source_meta=source_meta,
        )

    @app.route('/api/playback', methods=['POST'])
    def api_playback():
        payload = json_body(force=True)
        uri = library_service.canonicalize_uri(str(payload.get('uri') or ''))
        try:
            position, duration = float(payload.get('position')), float(payload.get('duration'))
        except (TypeError, ValueError):
            return {'error': 'position and duration must be numbers'}, 400
        if not (math.isfinite(position) and math.isfinite(duration)):
            return {'error': 'position and duration must be finite'}, 400
        if not uri or not library_service.resolve_path(uri):
            return {'error': 'Unknown media'}, 404
        return {'saved': activity_store.save_position(uri, position, duration)}

    @app.route('/image')
    def image_view():
        uri = request.args.get('uri')
        if not uri:
            return render_template('error.html', status=400,
                                   message=t('image.error.missing_uri')), 400
        uri = library_service.find_existing_uri(uri)

        target = library_service.resolve_path(uri)
        if not target or not target.exists():
            return render_template('error.html', status=404,
                                   message=t('image.error.file_not_found')), 404
        source_meta = download_manager.resolve_source_for_file(uri)
        uri_path_encoded = quote(uri, safe='/')
        uri_query_encoded = quote(uri, safe='')
        prev_item, next_item = neighbors('image', uri)
        return render_template(
            'image_detail.html',
            previous_url=f"/image?uri={quote(prev_item, safe='')}" if prev_item else None,
            next_url=f"/image?uri={quote(next_item, safe='')}" if next_item else None,
            image=target,
            uri=uri,
            uri_path_encoded=uri_path_encoded,
            uri_query_encoded=uri_query_encoded,
            stat=target.stat(),
            source_meta=source_meta,
        )

    def detail_url(uri):
        if Path(uri).suffix.lower() in IMAGE_EXTENSIONS:
            return f"/image?uri={quote(uri, safe='')}"
        return f"/detail/{quote(uri, safe='/')}"

    def purge(entry_id):
        uri = trash_service.purge(entry_id)
        # A purge can run long after the delete: a new file may already sit on this
        # URI (the same download name, restored from a backup). Its favourites,
        # collection membership and captions are not the purged file's leftovers.
        try:
            reused = library_service.resolve_path(uri) is not None
        except OSError:
            reused = False
        try:
            for candidate in library_service.legacy_candidates(uri):
                download_manager.delete_source_for_file(candidate)
            thumbnail_service.delete_thumbnail(uri)
        except OSError:
            # The trashed file is already gone; the derived cleanup is best effort and
            # must not turn a successful purge into an error response. Say so anyway:
            # a thumbnail that survives its media is served again for the next file
            # that lands on the same name.
            app.logger.warning('derived cleanup after purge failed for %s', uri, exc_info=True)
        # Saved lists are keyed by URI, so a leftover row silently re-attaches to the
        # next file that lands on the same name. Purge is the last point where the
        # file is known to be gone for good -- unless the name is already taken again.
        if reused:
            return
        try:
            if favorite_service is not None:
                favorite_service.set_favorite(uri, False)
            if collection_store is not None:
                for collection in collection_store.list_for_media(uri):
                    collection_store.remove_items(collection['id'], [uri])
            if image_metadata is not None:
                # Captions are keyed by URI too: a stale entry would caption the next
                # file that lands on this name, with text about a different picture.
                for candidate in library_service.legacy_candidates(uri):
                    image_metadata.delete(candidate)
        except Exception:  # noqa: BLE001 - derived cleanup must never fail the purge
            app.logger.warning('saved-list cleanup failed for %s', uri, exc_info=True)

    def purge_expired():
        for entry_id in trash_service.expired():
            try:
                purge(entry_id)
            except (KeyError, OSError):
                continue
            except Exception:  # noqa: BLE001 - runs at startup; one bad entry must not stop the app
                # purge_expired() runs while the routes are registered: a locked database
                # or a damaged entry escaped create_app() and the server never started.
                app.logger.warning('expired trash cleanup failed for %s', entry_id, exc_info=True)

    purge_expired()

    @app.route("/delete/<path:name>", methods=['POST', 'GET'])
    def delete_view(name):
        name = library_service.find_existing_uri(name)
        if request.method == 'GET':
            return render_template('delete_confirm.html', file=name)
        wants_json = request.accept_mimetypes.best == 'application/json'
        try:
            entry = trash_service.move(name)
        except FileNotFoundError:
            media_index.delete(library_service.canonicalize_uri(name))
            return ({'success': False, 'error': t('detail.error.file_not_found')}, 404) if wants_json else redirect('/library')
        except OSError as e:
            # A file that is still open (a video being played, on Windows also a
            # locked directory) is a conflict, not a server error, and str(e)
            # would leak the absolute media path to the client.
            in_use = (
                getattr(e, 'winerror', None) in (32, 33)
                or e.errno in (errno.EACCES, errno.EBUSY, errno.EPERM)
            )
            message = t('detail.error.file_in_use') if in_use else t('detail.error.delete_failed')
            return ({'error': message}, 409 if in_use else 500) if wants_json else (message, 409 if in_use else 500)
        try:
            media_index.delete(entry['uri'])
        except Exception:  # noqa: BLE001
            # The file is already in the trash, so the delete itself succeeded. A
            # failed index write used to surface as a 500 with a ghost tile that
            # opened to a 404; the next sync prunes unseen rows anyway.
            app.logger.warning('index delete failed for %s', entry['uri'], exc_info=True)
        return {'id': entry['id'], 'name': entry['name']} if wants_json else redirect('/library')

    @app.route('/api/trash')
    def api_trash():
        purge_expired()
        return {'items': trash_service.entries(), 'retention_days': trash_service.retention.days}

    @app.route('/api/trash/<entry_id>/restore', methods=['POST'])
    def api_trash_restore(entry_id):
        try:
            uri = trash_service.restore(entry_id)
        except KeyError:
            return {'success': False, 'error': t('detail.error.not_in_trash')}, 404
        except FileExistsError:
            return {'error': t('detail.error.trash_conflict')}, 409
        except OSError:
            # The trashed file is unreadable or its original folder cannot be
            # recreated (a locked directory on Windows): a conflict the user can
            # clear, not a 500 with a raw path in the message.
            return {'error': t('detail.error.trash_restore_failed')}, 409
        try:
            library_indexer.register_uris([uri])
        except Exception:  # noqa: BLE001
            # The file is back on disk and the trash entry is gone, so reporting a
            # failure here reads as "the file was lost". The index self-heals on the
            # next scan, so say so instead of raising.
            app.logger.warning('index register failed after restore for %s', uri, exc_info=True)
            return {'uri': uri, 'url': detail_url(uri), 'indexed': False}
        return {'uri': uri, 'url': detail_url(uri)}

    @app.route('/api/trash/<entry_id>', methods=['DELETE'])
    def api_trash_purge(entry_id):
        try:
            purge(entry_id)
        except KeyError:
            return {'success': False, 'error': t('detail.error.not_in_trash')}, 404
        except OSError:
            # A trashed file still open elsewhere (Windows) is a conflict, not a 500.
            return {'error': t('detail.error.trash_purge_failed')}, 409
        return {'ok': True}

    @app.route('/api/trash', methods=['DELETE'])
    def api_trash_empty():
        items = trash_service.entries()
        purged = 0
        failed = 0
        for item in items:
            try:
                purge(item['id'])
                purged += 1
            except KeyError:
                continue
            except OSError:
                # One locked file must not abort the whole purge.
                failed += 1
        result = {'purged': purged}
        if failed:
            result['failed'] = failed
        return result

    @app.route("/delete", methods=['POST', 'GET'])
    def delete_confirm_legacy():
        # Legacy support for query param style
        uri = request.args.get('uri')
        if not uri: return redirect('/library')
        return redirect(f"/delete/{quote(uri)}")

    def _media_validators(target):
        """The ``(etag, last_modified)`` pair for one file, or None when unreadable."""
        try:
            stat = target.stat()
        except OSError:
            return None
        # Same shape as ``werkzeug.utils.send_file`` so the 200 path and the
        # hand-built range paths answer with one identical validator.
        check = zlib.adler32(str(target).encode()) & 0xFFFFFFFF
        return f'{stat.st_mtime}-{stat.st_size}-{check}', formatdate(stat.st_mtime, usegmt=True)

    def _if_range_allows_range(value, target):
        """Whether an ``If-Range`` validator still selects the same representation."""
        validators = _media_validators(target)
        if not validators:
            return False
        etag, last_modified = validators
        candidate = value.strip()
        if candidate.startswith('W/'):
            # RFC 9110: a weak validator may never be used to select a range.
            return False
        if candidate.startswith('"') and candidate.endswith('"') and len(candidate) > 1:
            candidate = candidate[1:-1]
        return candidate == etag or value.strip() == last_modified

    def _whole_file_media(target):
        """Answer 200 for a file whose Range must be ignored, off the worker thread.

        Reached when ``If-Range`` no longer matches: RFC 9110 then requires the whole
        representation, and ``send_file()`` would stream it through the generator-based
        ``RangeWrapper`` that waitress copies in the request thread. A client asking for
        one byte of a 2 GB file with a stale validator could pin a worker for the whole
        transfer, so the body is built here and moved off-thread by the file wrapper.
        """
        try:
            size = target.stat().st_size
            handle = open(target, 'rb')
        except OSError:
            return None
        wrapper = request.environ.get('wsgi.file_wrapper')
        body = None
        if wrapper is not None:
            try:
                body = wrapper(handle, 64 * 1024)
            except Exception:  # noqa: BLE001 - a hostile wrapper must not leak the handle
                body = None
        if body is None:
            body = _iter_reader(_BoundedReader(handle, 0, size))
        response = Response(body, status=200, direct_passthrough=True)
        response.headers['Content-Length'] = str(size)
        response.headers['Accept-Ranges'] = 'bytes'
        response.headers['Cache-Control'] = 'private, no-cache'
        response.mimetype = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
        return _apply_media_validators(response, target)

    def _apply_media_validators(response, target):
        """Give hand-built media responses the same validators as ``send_file``.

        The 200 path goes through ``send_file``, which sets ``ETag`` and
        ``Last-Modified``; the 206/416 paths build the response by hand and used to
        ship without either. A player that revalidates a partially downloaded range
        then had nothing to compare and re-downloaded the whole file.
        """
        validators = _media_validators(target)
        if validators is None:
            return response
        etag, last_modified = validators
        response.set_etag(etag)
        response.headers['Last-Modified'] = last_modified
        return response

    def _ranged_media(target):
        """Serve a single byte range through ``wsgi.file_wrapper``.

        Werkzeug's ``RangeWrapper`` is a plain generator, so waitress copies it in
        the request thread. A client that stops reading (a paused video tab, a phone
        that lost signal) then pins a worker until ``channel_timeout``; eight of them
        starve the pool and the whole UI stops answering. Handing the file object to
        the server's file wrapper lets waitress move the bytes off-thread.
        """
        if request.method != 'GET':
            return None
        if request.headers.get('If-None-Match') or request.headers.get('If-Modified-Since'):
            return None  # send_file owns the 304 decision
        if_range = (request.headers.get('If-Range') or '').strip()
        if if_range and not _if_range_allows_range(if_range, target):
            # RFC 9110: a stale If-Range makes the server ignore the Range and send the
            # whole representation. send_file() would stream that body through the
            # generator-based RangeWrapper that waitress copies in the request thread,
            # so a client asking for one byte of a 2 GB file with a stale validator
            # pinned this worker for the whole transfer. Build the full response here.
            return _whole_file_media(target)
        try:
            size = target.stat().st_size
        except OSError:
            # The file was deleted (or unmounted) between the exists() check in
            # serve_media() and here: dragging the seek bar then answered 500 with a
            # JSON error body, which the player cannot recover from. Fall through to
            # send_file(), which turns the same condition into a plain 404.
            return None
        span = _byte_range(request.headers.get('Range'), size)
        if span == 'unsatisfiable':
            # RFC 9110 wants 416 here; silently sending the whole file made a broken
            # seek look like a successful read to the player.
            response = Response(status=416)
            response.headers['Content-Range'] = f'bytes */{size}'
            response.headers['Accept-Ranges'] = 'bytes'
            return _apply_media_validators(response, target)
        if span == 'ignore':
            # An unsupported Range (multi-range, bad syntax) must not look like a
            # missing file: RFC 9110 lets the server ignore it and send everything.
            return send_file(target, conditional=False)
        start, end = span
        length = end - start + 1
        wrapper = request.environ.get('wsgi.file_wrapper')
        if wrapper is not None:
            # waitress only moves a body off the worker thread when the app_iter
            # answers ``prepare(cl)`` with a positive size, which requires a seekable
            # file object. ``_BoundedReader`` is not seekable, so wrapping it silently
            # fell back to copying every 206 in the request thread — exactly the
            # "paused video pins a worker" bug this branch was written to fix. Handing
            # the real handle positioned at ``start`` keeps the fast path; the bounded
            # ``Content-Length`` below stops it at ``end``.
            try:
                handle = open(target, 'rb')
                handle.seek(start)
            except OSError:
                return None  # deleted mid-play: send_file() answers 404
            body = wrapper(handle, 64 * 1024)
        else:
            # No file wrapper (test client, other WSGI servers): read exactly the
            # requested window, a bare file object would stream to EOF.
            try:
                handle = open(target, 'rb')
            except OSError:
                return None
            body = _iter_reader(_BoundedReader(handle, start, length))
        response = Response(body, status=206, direct_passthrough=True)
        response.headers['Content-Range'] = f'bytes {start}-{end}/{size}'
        response.headers['Content-Length'] = str(length)
        response.headers['Accept-Ranges'] = 'bytes'
        response.headers['Cache-Control'] = 'private, no-cache'
        response.mimetype = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
        return _apply_media_validators(response, target)

    @app.route("/media/<path:filename>")
    def serve_media(filename):
        target = library_service.resolve_path(filename)
        if not target or not target.exists() or not target.is_file():
            return t('image.error.file_not_found'), 404
        if request.args.get('download') == '1':
            # The download button must save the file, including the formats the
            # browser would otherwise render inline. No Range handling here: a
            # resumed partial read of an attachment is not what was asked for.
            return send_file(target, mimetype='application/octet-stream',
                             as_attachment=True, download_name=target.name)
        # 只对已知音视频/图片内联下发：媒体库是用户可写目录，一个 .svg/.html 落进来
        # 再给个链接，脚本就会在本应用源内执行（可带 cookie 调 /api/*）。
        if target.suffix.lower() in INLINE_MEDIA_SUFFIXES:
            if request.headers.get('Range'):
                ranged = _ranged_media(target)
                if ranged is not None:
                    return ranged
            return send_file(target)
        return send_file(target, mimetype='application/octet-stream', as_attachment=True,
                         download_name=target.name)

    @app.route("/media")
    def serve_media_legacy():
        # Legacy support for /media?uri=...
        uri = request.args.get('uri')
        if not uri: return "Missing uri", 400
        return redirect(f"/media/{quote(library_service.find_existing_uri(uri), safe='/')}")

    @app.route('/api/media/info')
    def api_media_info():
        """Everything the flow's info sheet shows for one work, in one round trip."""
        uri = str(request.args.get('uri') or '').strip()
        if not uri:
            return {'success': False, 'error': t('image.error.missing_uri')}, 400
        canonical = library_service.find_existing_uri(uri) or uri
        target = library_service.resolve_path(canonical)
        if not target or not target.exists() or not target.is_file():
            return {'success': False, 'error': t('image.error.file_not_found')}, 404
        try:
            size_bytes = target.stat().st_size
        except OSError:
            size_bytes = 0
        record = {}
        try:
            rows = media_index.records_for_names([canonical]) if media_index else []
            record = rows[0] if rows else {}
        except Exception:  # a broken index must not hide the file it points at
            record = {}
        media_type = str(record.get('media_type') or '') or (
            'video' if target.suffix.lower() in VIDEO_EXTENSIONS else 'image'
        )
        width = int(record.get('width') or 0)
        height = int(record.get('height') or 0)
        album_index = record.get('album_index')
        album_size = int(record.get('album_size') or 0)
        live_uri = str(record.get('live_video_uri') or '')
        duration = format_duration(probe_media_duration(library_service, canonical, media_type))
        return {
            'success': True,
            'data': {
                'name': canonical,
                'path': str(target),
                'file_name': target.name,
                'suffix': target.suffix.lower().lstrip('.'),
                'size_bytes': size_bytes,
                'size_label': format_size(size_bytes),
                'media_type': media_type,
                'width': width,
                'height': height,
                'duration_label': duration,
                'post_date': str(record.get('captured_local_date') or '')[:10],
                'album_index': (int(album_index) + 1) if album_index is not None else 0,
                'album_size': album_size,
                'live_video_uri': live_uri,
                'media_url': media_urls(canonical)['media_url'],
            },
        }

    def _placeholder_response():
        # A failed generation (ffmpeg missing, file being copied, unknown uri) must
        # not be cached for an hour: the browser would keep showing the placeholder
        # long after the thumbnail became generatable.
        response = send_file(io.BytesIO(thumbnail_service.placeholder), mimetype='image/png')
        response.headers['Cache-Control'] = 'private, no-store'
        return response

    @app.route('/thumb')
    def thumb_view():
        uri = request.args.get('uri')
        if not uri: return _placeholder_response()

        path, mimetype = thumbnail_service.get_thumbnail(library_service.find_existing_uri(uri))
        if isinstance(path, bytes):
            # get_thumbnail() returns the placeholder bytes when nothing usable exists.
            if mimetype == 'image/png':
                return _placeholder_response()
            return send_file(io.BytesIO(path), mimetype=mimetype)
        # Hand the bytes over instead of the path: on Windows a response that keeps
        # the cache file open makes the next regeneration fail to replace it.
        try:
            stat = path.stat()
            payload = path.read_bytes()
        except OSError:
            return _placeholder_response()
        response = send_file(io.BytesIO(payload), mimetype=mimetype, max_age=0)
        # max-age without a validator means "must-revalidate" never actually
        # revalidates; the cache file identity is the cheapest correct validator.
        response.set_etag(f'{stat.st_mtime_ns:x}-{stat.st_size:x}')
        # 换封面后列表里的缩略图要能立刻更新：一小时 max-age 会让浏览器完全不发请求，
        # 改成 no-cache 后每次用 ETag 条件请求（未变则是 304，几乎零成本）。
        response.headers['Cache-Control'] = 'private, no-cache'
        return response.make_conditional(request.environ)

    @app.route('/api/image/embedded-metadata')
    def api_image_embedded_metadata():
        uri = request.args.get('uri')
        if not uri:
            return {'success': False, 'error': t('image.error.missing_uri')}, 400
        canonical_uri = library_service.find_existing_uri(uri)
        target = library_service.resolve_path(canonical_uri)
        if not target or not target.exists():
            return {'success': False, 'error': t('image.error.file_not_found')}, 404
        return {'success': True, 'data': {'embedded_generation': read_embedded_generation(target)}}

    @app.route('/api/thumbnail/<path:name>', methods=['POST'])
    def api_set_thumbnail(name):
        name = library_service.find_existing_uri(name)
        if not name:
            return {'success': False, 'error': t('detail.error.file_not_found')}, 404
        target = library_service.resolve_path(name)
        if not target or not target.exists():
            return {'success': False, 'error': t('detail.error.file_not_found')}, 404
        if not target.is_file():
            return {'success': False, 'error': t('detail.error.invalid_path')}, 400

        payload = json_body()
        ts = payload.get('time')
        timestamp = None
        if ts is not None:
            try:
                timestamp = float(ts)
            except (TypeError, ValueError):
                return {'success': False, 'error': t('detail.error.invalid_time')}, 400
            # Reject NaN/inf/negative before ffmpeg sees them: it used to fail deep in
            # the pipeline and surface as an opaque 500.
            if not (0 <= timestamp < 10 ** 9):
                return {'success': False, 'error': t('detail.error.invalid_time')}, 400

        # overwrite=True: without it generate_thumbnail() sees the existing poster,
        # decides it is fresh and returns it, so the chosen frame was silently
        # dropped while the response claimed success.
        success = thumbnail_service.generate_thumbnail(name, timestamp=timestamp, overwrite=True)

        if success:
             return {'success': True, 'url': f"{media_urls(name)['thumb_url']}&v={time.time_ns()}"}
        # A cover that cannot be generated is a per-file failure, not a server bug.
        return {'success': False, 'error': t('detail.error.thumbnail_generate')}, 422
