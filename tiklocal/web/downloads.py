from flask import render_template, request

from tiklocal.i18n import t
from tiklocal.services.downloader import (
    DEFAULT_DOWNLOAD_CONFIG, validate_download_config, validate_download_url,
)
from tiklocal.services.tiktok_downloader import TikTokDownloaderError
from tiklocal.services.url_list import UrlListStore, format_text, parse_text


def register_download_routes(app, download_manager, library_service, tiktok_service=None):
    def build_download_config_payload():
        config = download_manager.get_config()
        effective = {key: config.get(key) for key in DEFAULT_DOWNLOAD_CONFIG.keys()}
        payload = {
            'config': config,
            'defaults': dict(DEFAULT_DOWNLOAD_CONFIG),
            'effective': effective,
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

        payload = request.get_json(silent=True) or {}
        validated, error = validate_download_config(payload, partial=True)
        if error:
            return {'success': False, 'error': error}, 400

        try:
            download_manager.update_config(validated)
        except ValueError as exc:
            return {'success': False, 'error': str(exc)}, 400

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
            return {'success': False, 'error': str(exc)}, 400
        return {'success': True, 'data': data}

    def url_list_store():
        return UrlListStore()

    @app.route('/api/download/urls', methods=['GET', 'POST'])
    def api_download_urls():
        store = url_list_store()
        if request.method == 'GET':
            return {'success': True, 'data': store.snapshot()}

        payload = request.get_json(silent=True) or {}
        action = str(payload.get('action') or 'replace')
        text = str(payload.get('text') or '')
        raw_lines = payload.get('lines')
        lines = [str(item) for item in raw_lines] if isinstance(raw_lines, list) else parse_text(text)
        try:
            if action == 'replace':
                store.replace(lines)
            elif action == 'add':
                store.add(lines)
            elif action == 'remove':
                store.remove(str(payload.get('url') or ''))
            elif action == 'clear':
                store.clear()
            elif action == 'source':
                store.set_source_path(str(payload.get('source_path') or ''))
            else:
                return {'success': False, 'error': t('download.urls.error')}, 400
        except ValueError as exc:
            return {'success': False, 'error': str(exc)}, 400
        except OSError as exc:
            return {'success': False, 'error': str(exc)}, 400

        return {'success': True, 'data': store.snapshot()}

    @app.route('/api/download/urls/export')
    def api_download_urls_export():
        body = format_text(url_list_store().lines())
        response = app.response_class(body, mimetype='text/plain; charset=utf-8')
        response.headers['Content-Disposition'] = 'attachment; filename="download-urls.txt"'
        return response

    @app.route('/api/download/urls/sync', methods=['POST'])
    def api_download_urls_sync():
        payload = request.get_json(silent=True) or {}
        direction = str(payload.get('direction') or 'pull')
        store = url_list_store()
        try:
            if 'source_path' in payload:
                store.set_source_path(str(payload.get('source_path') or ''))
            if direction == 'pull':
                result = store.pull()
            elif direction == 'push':
                result = store.push()
            else:
                return {'success': False, 'error': t('download.urls.error')}, 400
        except ValueError as exc:
            return {'success': False, 'error': str(exc)}, 400
        except OSError as exc:
            return {'success': False, 'error': str(exc)}, 400

        data = store.snapshot()
        data['result'] = result
        return {'success': True, 'data': data}

    @app.route('/api/download/urls/queue', methods=['POST'])
    def api_download_urls_queue():
        payload = request.get_json(silent=True) or {}
        engine = str(payload.get('engine') or 'yt-dlp')
        save_mode = str(payload.get('save_mode') or 'root')
        store = url_list_store()

        queued: list[dict] = []
        failed: list[dict] = []
        for url in store.lines():
            try:
                job = download_manager.enqueue(url, save_mode=save_mode, engine=engine)
            except (RuntimeError, ValueError) as exc:
                failed.append({'url': url, 'error': str(exc)})
                continue
            queued.append({'url': url, 'job_id': job.get('id', '')})

        return {'success': True, 'data': {'queued': len(queued), 'failed': failed, 'items': queued}}

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

        payload = request.get_json(silent=True) or {}
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
            return {'success': False, 'error': 'TikTokDownloader service is not configured.'}, 400

        payload = request.get_json(silent=True) or {}
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
            return {'success': False, 'error': str(exc)}, 400
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
            return {'success': True, 'data': {'tiktok_downloader': section}}

        payload = request.get_json(silent=True) or {}
        section = payload.get('tiktok_downloader')
        if section is None:
            section = payload
        if not isinstance(section, dict):
            return {'success': False, 'error': t('download.error.tiktok_config_object')}, 400

        try:
            download_manager.update_config({'tiktok_downloader': section})
        except ValueError as exc:
            return {'success': False, 'error': str(exc)}, 400
        updated = download_manager.get_config().get('tiktok_downloader') or {}
        return {'success': True, 'data': {'tiktok_downloader': updated}}

    @app.route('/api/download/jobs/<job_id>')
    def api_download_job_detail(job_id):
        job = download_manager.get_job(job_id)
        if not job:
            return {'success': False, 'error': 'Job not found'}, 404
        return {'success': True, 'data': {'job': job}}

    @app.route('/api/download/jobs/<job_id>/cancel', methods=['POST'])
    def api_download_job_cancel(job_id):
        job = download_manager.cancel(job_id)
        if not job:
            return {'success': False, 'error': 'Job not found'}, 404
        return {'success': True, 'data': {'job': job}}

    @app.route('/api/download/jobs/<job_id>', methods=['DELETE'])
    def api_download_job_delete(job_id):
        ok, error = download_manager.delete_job(job_id)
        if not ok:
            status = 404 if error == 'Job not found' else 400
            return {'success': False, 'error': error}, status
        return {'success': True, 'data': {'deleted': True}}

    @app.route('/api/download/jobs/clear', methods=['POST'])
    def api_download_jobs_clear():
        deleted = download_manager.clear_history()
        return {'success': True, 'data': {'deleted': deleted}}

    @app.route('/api/download/jobs/<job_id>/retry', methods=['POST'])
    def api_download_job_retry(job_id):
        job, error = download_manager.retry_job(job_id)
        if error:
            status = 404 if error == 'Job not found' else 400
            return {'success': False, 'error': error}, status
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
        payload = request.get_json(silent=True) or {}
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
