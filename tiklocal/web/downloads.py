import os

from flask import render_template, request

from tiklocal.i18n import t
from tiklocal.services.downloader import (
    DEFAULT_DOWNLOAD_CONFIG, MASKED_SECRET, TERMINAL_JOB_STATUS, validate_download_config,
    validate_download_url,
)
from tiklocal.services.tiktok_downloader import TikTokDownloaderError
from tiklocal.services.url_list import UrlListStore, format_text, parse_text
from tiklocal.web import json_body


# UrlListStore raises English ValueErrors that used to be echoed to the browser.
# Map them back to localized copy so a zh-CN UI never shows raw service text.
_URL_LIST_ERROR_KEYS = (
    ('Too many URLs', 'download.urls.error_too_many'),
    ('must be absolute', 'download.urls.error_path_absolute'),
    ('must point at a file', 'download.urls.error_path_folder'),
    ('plain text list', 'download.urls.error_path_text'),
    ('application state file', 'download.urls.error_path_state'),
    ('No server path configured', 'download.urls.error_no_path'),
    ('too large to import', 'download.urls.error_too_large'),
    ('Unable to read', 'download.urls.error_read'),
    ('Unable to write', 'download.urls.error_write'),
)


def url_list_error_message(exc):
    message = str(exc)
    for marker, key in _URL_LIST_ERROR_KEYS:
        if marker in message:
            return t(key)
    return t('download.urls.error')


def register_download_routes(app, download_manager, library_service, tiktok_service=None,
                             trash_service=None, media_index=None):
    def mask_secrets(config):
        """Never ship the sidecar token to the browser; report its presence instead."""
        if not isinstance(config, dict):
            return config
        section = config.get('tiktok_downloader')
        if not isinstance(section, dict):
            return config
        masked = dict(config)
        masked_section = dict(section)
        token = masked_section.get('token') or ''
        masked_section['token'] = MASKED_SECRET if token else ''
        masked_section['has_token'] = bool(token)
        masked['tiktok_downloader'] = masked_section
        return masked

    def build_download_config_payload():
        config = download_manager.get_config()
        effective = {key: config.get(key) for key in DEFAULT_DOWNLOAD_CONFIG.keys()}
        payload = {
            'config': mask_secrets(config),
            'defaults': mask_secrets(dict(DEFAULT_DOWNLOAD_CONFIG)),
            'effective': mask_secrets(effective),
            # The configured download_source may be a typo or a removed source;
            # surface where files actually land so the UI can show it.
            'output_source_id': getattr(download_manager, 'output_source_id', ''),
            # The folder name, not the host path: this payload is served to every
            # signed-in browser on the LAN, and the absolute path carries the
            # Windows user name (the settings API drops the data path for this reason).
            'output_source_path': os.path.basename(str(getattr(download_manager, 'media_root', '') or '')),
        }
        return payload

    def tiktok_status_snapshot():
        if tiktok_service is None:
            return {'configured': False, 'base_url': '', 'reachable': False,
                    'message': 'TikTokDownloader service is not configured.'}
        try:
            return tiktok_service.status()
        except Exception as exc:  # The download page must still load when probing fails.
            return {'configured': False, 'base_url': '', 'reachable': False,
                    'message': f'Unable to check TikTokDownloader: {exc}'}

    @app.route('/download')
    def download_view():
        """URL Download Center"""
        return render_template('download.html', menu='download')

    @app.route('/api/download/probe', methods=['GET', 'POST'])
    def api_download_probe():
        data = download_manager.probe_dependencies()
        data['tiktok_downloader'] = tiktok_status_snapshot()
        return {'success': True, 'data': data}

    @app.route('/api/download/config', methods=['GET', 'POST'])
    def api_download_config():
        if request.method == 'GET':
            return {'success': True, 'data': build_download_config_payload()}

        payload = json_body()
        validated, error = validate_download_config(payload, partial=True)
        if error:
            return {'success': False, 'error': error}, 400

        try:
            download_manager.update_config(validated)
        except ValueError as exc:
            # This is the download-config route: logging "URL list request" here sent
            # every investigation of a rejected config to the wrong feature.
            app.logger.warning('Download config rejected: %s', exc)
            return {'success': False, 'error': url_list_error_message(exc)}, 400

        return {'success': True, 'data': build_download_config_payload()}

    @app.route('/api/download/cookies')
    def api_download_cookies():
        return {'success': True, 'data': download_manager.list_cookie_files()}

    @app.route('/api/download/cookies/upload', methods=['POST'])
    def api_download_cookies_upload():
        file = request.files.get('file')
        if not file:
            return {'success': False, 'error': t('download.error.no_upload_file')}, 400

        filename = str(file.filename or '').strip()
        if not filename:
            return {'success': False, 'error': t('download.error.filename_empty')}, 400

        content = file.read()
        try:
            # Uploading consistently replaces a file with the same name.
            data = download_manager.upload_cookie_file(filename, content, replace=True)
        except ValueError as exc:
            app.logger.warning('Cookie upload rejected: %s', exc)
            return {'success': False, 'error': url_list_error_message(exc)}, 400
        return {'success': True, 'data': data}

    def url_list_store():
        return UrlListStore()

    @app.route('/api/download/urls', methods=['GET', 'POST'])
    def api_download_urls():
        store = url_list_store()
        if request.method == 'GET':
            # A list file held open by another process (AV scan, editor) is a
            # transient storage problem, not a server bug: report it as 503 like
            # the write paths do instead of letting the OSError escape as a 500.
            try:
                return {'success': True, 'data': store.snapshot()}
            except OSError as exc:
                app.logger.warning('Could not read the download URL list', exc_info=exc)
                return {'success': False, 'error': t('common.error.storage_unavailable')}, 503

        payload = json_body()
        action = str(payload.get('action') or '')
        text = str(payload.get('text') or '')
        raw_lines = payload.get('lines')
        if not action:
            # A body without an explicit action used to default to "replace", so
            # any caller that forgot the field wiped the whole list and still got
            # a success envelope back. Require the caller to say what it wants.
            return {'success': False, 'error': t('download.urls.error_missing_action')}, 400
        if action == 'replace' and not text and not isinstance(raw_lines, list):
            return {'success': False, 'error': t('download.urls.error')}, 400
        lines = [str(item) for item in raw_lines] if isinstance(raw_lines, list) else parse_text(text)
        counts: dict[str, int] | None = None
        try:
            if action == 'replace':
                store.replace(lines)
            elif action == 'add':
                # Count on the raw lines: parse_text already dropped the invalid
                # ones, which made "imported N" report the list length instead of
                # what this import really added.
                raw_add = ([str(item) for item in raw_lines] if isinstance(raw_lines, list)
                           else text.splitlines())
                _, added, rejected, truncated = store.add_reporting(raw_add)
                counts = {'added': added, 'rejected': rejected, 'truncated': truncated}
            elif action == 'remove':
                store.remove(str(payload.get('url') or ''))
            elif action == 'clear':
                store.clear()
            elif action == 'source':
                store.set_source_path(str(payload.get('source_path') or ''))
            else:
                return {'success': False, 'error': t('download.urls.error')}, 400
        except ValueError as exc:
            app.logger.warning('URL list source path rejected: %s', exc)
            return {'success': False, 'error': url_list_error_message(exc)}, 400
        except OSError as exc:
            app.logger.warning('Could not write the download URL list', exc_info=exc)
            return {'success': False, 'error': t('common.error.storage_unavailable')}, 503

        data = store.snapshot()
        if counts:
            data.update(counts)
        return {'success': True, 'data': data}

    @app.route('/api/download/urls/export')
    def api_download_urls_export():
        try:
            body = format_text(url_list_store().lines())
        except OSError as exc:
            app.logger.warning('Could not read the download URL list', exc_info=exc)
            return {'success': False, 'error': t('common.error.storage_unavailable')}, 503
        response = app.response_class(body, mimetype='text/plain')
        response.headers['Content-Disposition'] = 'attachment; filename="download-urls.txt"'
        return response

    @app.route('/api/download/urls/sync', methods=['POST'])
    def api_download_urls_sync():
        payload = json_body()
        direction = str(payload.get('direction') or 'pull')
        if direction not in ('pull', 'push'):
            return {'success': False, 'error': t('download.urls.error')}, 400
        store = url_list_store()
        try:
            # Validate the direction first: a rejected request used to persist
            # source_path anyway (and an empty string silently wiped it).
            source = str(payload.get('source_path') or '').strip()
            if source:
                store.set_source_path(source)
            if direction == 'pull':
                result = store.pull()
            else:
                result = store.push()
        except ValueError as exc:
            app.logger.warning('URL list sync rejected: %s', exc)
            return {'success': False, 'error': url_list_error_message(exc)}, 400
        except OSError as exc:
            app.logger.warning('Could not write the download URL list', exc_info=exc)
            return {'success': False, 'error': t('common.error.storage_unavailable')}, 503

        data = store.snapshot()
        data['result'] = result
        return {'success': True, 'data': data}

    @app.route('/api/download/urls/queue', methods=['POST'])
    def api_download_urls_queue():
        payload = json_body()
        engine = str(payload.get('engine') or 'yt-dlp')
        save_mode = str(payload.get('save_mode') or 'root')
        store = url_list_store()

        queued: list[dict] = []
        failed: list[dict] = []
        try:
            stored_urls = store.lines()
        except OSError as exc:
            # The GET and export routes already answered 503 for an unreadable store;
            # this one let the exception escape as a 500 traceback.
            app.logger.warning('Could not read the download URL list', exc_info=exc)
            return {'success': False, 'error': t('common.error.storage_unavailable')}, 503
        queued: list[dict] = []
        skipped: list[dict] = []
        for url in stored_urls:
            # Same checks as a single-URL queue: the list endpoint must not be a
            # bypass for localhost/metadata targets or non-http schemes.
            validated, error = validate_download_url({
                'url': url, 'save_mode': save_mode, 'engine': engine,
            })
            if error:
                failed.append({'url': url, 'error': error})
                continue
            try:
                job = download_manager.enqueue(validated['url'], save_mode=validated['save_mode'],
                                               engine=validated.get('engine', engine))
            except (RuntimeError, ValueError) as exc:
                failed.append({'url': url, 'error': str(exc)})
                continue
            entry = {'url': url, 'job_id': job.get('id', '')}
            # The manager hands back the active job for a URL that is already queued.
            # Counting it as "queued" made a partial retry look like it had started
            # the whole list again, so the caller keeps re-submitting the successes.
            (skipped if job.get('deduplicated') else queued).append(entry)

        if not queued and failed:
            # Reporting success with queued=0 made a fully rejected batch look
            # like it started; the caller has no way to notice.
            return {'success': False, 'error': t('download.urls.queue_rejected', count=len(failed)),
                    'data': {'queued': 0, 'skipped': skipped, 'failed': failed, 'items': queued}}, 400

        return {'success': True, 'data': {'queued': len(queued), 'skipped': skipped,
                                          'failed': failed, 'items': queued}}

    @app.route('/api/download/jobs', methods=['GET', 'POST'])
    def api_download_jobs():
        if request.method == 'GET':
            limit = request.args.get('limit', 50)
            try:
                limit_value = int(limit)
            except (TypeError, ValueError):
                limit_value = 50
            jobs = download_manager.list_jobs(limit=limit_value)
            return {'success': True, 'data': {'jobs': jobs}}

        payload = json_body()
        validated, error = validate_download_url(payload)
        if error:
            return {'success': False, 'error': error}, 400

        try:
            job = download_manager.enqueue(
                validated['url'],
                save_mode=validated['save_mode'],
                engine=validated.get('engine', 'yt-dlp'),
                cookie_mode=validated.get('cookie_mode', 'auto'),
                cookie_file=validated.get('cookie_file', ''),
            )
        except RuntimeError as exc:
            return {'success': False, 'error': str(exc)}, 400

        return {'success': True, 'data': {'job': job}}

    @app.route('/api/download/tiktok', methods=['POST'])
    def api_download_tiktok():
        if tiktok_service is None:
            return {'success': False, 'error': t('download.alert.tiktok_unconfigured')}, 400

        payload = json_body()
        url = str(payload.get('url') or '').strip()
        if not url:
            return {'success': False, 'error': t('download.error.url_empty')}, 400
        tab = str(payload.get('tab') or 'post').strip() or 'post'
        try:
            count = int(payload.get('count') or 18)
        except (TypeError, ValueError):
            count = 18

        try:
            data = tiktok_service.enqueue_from_url(url, tab=tab, count=count)
        except ValueError as exc:
            app.logger.warning('TikTok download enqueue rejected: %s', exc)
            return {'success': False, 'error': url_list_error_message(exc)}, 400
        except TikTokDownloaderError as exc:
            return {'success': False, 'error': str(exc)}, 502
        return {'success': True, 'data': data}

    @app.route('/api/download/tiktok/status')
    def api_download_tiktok_status():
        return {'success': True, 'data': tiktok_status_snapshot()}

    @app.route('/api/download/tiktok/config', methods=['GET', 'POST'])
    def api_download_tiktok_config():
        if request.method == 'GET':
            section = download_manager.get_config().get('tiktok_downloader') or {}
            return {'success': True, 'data': {'tiktok_downloader': mask_secrets({'tiktok_downloader': section})['tiktok_downloader']}}

        payload = json_body()
        section = payload.get('tiktok_downloader')
        if section is None:
            section = payload
        if not isinstance(section, dict):
            return {'success': False, 'error': t('download.error.tiktok_config_object')}, 400

        try:
            download_manager.update_config({'tiktok_downloader': section})
        except ValueError as exc:
            # validate_download_config already returns a localized, field-specific
            # message; mapping it through url_list_error_message() replaced it with
            # the generic URL-list error, so the form said "invalid URL" for a bad
            # timeout value.
            return {'success': False, 'error': str(exc) or t('download.error.tiktok_config_object')}, 400
        updated = download_manager.get_config().get('tiktok_downloader') or {}
        return {'success': True, 'data': {'tiktok_downloader': mask_secrets({'tiktok_downloader': updated})['tiktok_downloader']}}

    @app.route('/api/download/jobs/<job_id>')
    def api_download_job_detail(job_id):
        job = download_manager.get_job(job_id)
        if not job:
            return {'success': False, 'error': t('download.error.job_not_found')}, 404
        return {'success': True, 'data': {'job': job}}

    @app.route('/api/download/jobs/<job_id>/cancel', methods=['POST'])
    def api_download_job_cancel(job_id):
        job = download_manager.cancel(job_id)
        if not job:
            return {'success': False, 'error': t('download.error.job_not_found')}, 404
        return {'success': True, 'data': {'job': job}}

    @app.route('/api/download/jobs/<job_id>', methods=['DELETE'])
    def api_download_job_delete(job_id):
        # Decide 404 by existence, not by comparing an error string: the message is
        # localized now, and a string match would silently degrade to 400.
        exists = download_manager.get_job(job_id) is not None
        ok, error = download_manager.delete_job(job_id)
        if not ok:
            return {'success': False, 'error': error}, (400 if exists else 404)
        return {'success': True, 'data': {'deleted': True}}

    @app.route('/api/download/jobs/clear', methods=['POST'])
    def api_download_jobs_clear():
        deleted = download_manager.clear_history()
        return {'success': True, 'data': {'deleted': deleted}}

    def delete_downloaded_files(uris):
        """Move the given media files into the recoverable trash."""
        moved = 0
        missing = 0
        failed = []
        for uri in dict.fromkeys(uris):
            if trash_service is None:
                failed.append({'uri': uri, 'error': 'Trash is not available.'})
                continue
            try:
                entry = trash_service.move(uri)
            except FileNotFoundError:
                # The record may outlive the file (manual cleanup, moved media).
                missing += 1
                if media_index is not None:
                    media_index.delete(library_service.canonicalize_uri(uri))
                continue
            except (OSError, KeyError) as exc:
                # str(OSError) carries the absolute path and the Windows user name
                # straight into the response; the client never reads it anyway.
                app.logger.warning('Could not move %s to the trash', uri, exc_info=exc)
                failed.append({'uri': uri, 'error': t('download.error.file_move_failed')})
                continue
            moved += 1
            if media_index is not None:
                media_index.delete(entry['uri'])
        return {'moved': moved, 'missing': missing, 'failed': failed}

    @app.route('/api/download/jobs/delete', methods=['POST'])
    def api_download_jobs_delete():
        payload = json_body()
        raw_ids = payload.get('ids')
        if not isinstance(raw_ids, list):
            return {'success': False, 'error': t('download.error.ids_array')}, 400

        job_ids = []
        for item in raw_ids:
            value = str(item or '').strip()
            if value and value not in job_ids:
                job_ids.append(value)
            if len(job_ids) >= 200:
                break
        if not job_ids:
            return {'success': False, 'error': t('download.error.ids_empty')}, 400

        delete_files = bool(payload.get('delete_files'))
        file_result = {'moved': 0, 'missing': 0, 'failed': []}
        if delete_files:
            uris = []
            for job_id in job_ids:
                job = download_manager.get_job(job_id)
                if not job or job.get('status') not in TERMINAL_JOB_STATUS:
                    continue
                uris.extend(
                    str(value) for value in job.get('output_files_rel') or [] if str(value).strip()
                )
            # A retried or duplicated job can share output files with the one being
            # removed; deleting the file for the removed job left the surviving card
            # pointing at a file that had already moved to the trash.
            if uris:
                doomed = set(job_ids)
                still_used = {
                    str(value)
                    for other in download_manager.list_jobs(limit=200)
                    if other.get('id') not in doomed
                    for value in other.get('output_files_rel') or []
                }
                uris = [uri for uri in uris if uri not in still_used]
            file_result = delete_downloaded_files(uris)

        result = download_manager.delete_jobs(job_ids)
        data = {
            'deleted': result['deleted'],
            'skipped': result['skipped'],
            'files': file_result,
        }
        return {'success': True, 'data': data}

    @app.route('/api/download/jobs/<job_id>/retry', methods=['POST'])
    def api_download_job_retry(job_id):
        exists = download_manager.get_job(job_id) is not None
        job, error = download_manager.retry_job(job_id)
        if error:
            return {'success': False, 'error': error}, (400 if exists else 404)
        return {'success': True, 'data': {'job': job}}

    @app.route('/api/source')
    def api_source_single():
        file_rel = str(request.args.get('file', '')).strip()
        if not file_rel:
            return {'success': False, 'error': t('download.error.file_empty')}, 400
        file_rel = library_service.find_existing_uri(file_rel)
        source = download_manager.resolve_source_for_file(file_rel)
        return {'success': True, 'data': {'file': file_rel, 'source': source}}

    @app.route('/api/source/batch', methods=['POST'])
    def api_source_batch():
        payload = json_body()
        files = payload.get('files')
        if not isinstance(files, list):
            return {'success': False, 'error': t('download.error.files_array')}, 400

        normalized: list[str] = []
        response_keys: list[tuple[str, str]] = []
        for item in files:
            value = str(item or '').strip()
            if value:
                canonical = library_service.find_existing_uri(value)
                normalized.append(canonical)
                response_keys.append((value, canonical))
            if len(normalized) >= 200:
                break
        resolved_items = download_manager.resolve_sources_for_files(normalized)
        items = {original: resolved_items.get(canonical) for original, canonical in response_keys}
        return {'success': True, 'data': {'items': items}}
