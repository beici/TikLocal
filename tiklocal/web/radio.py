"""Browser Radio: stations, audio metadata, artwork and listening feedback."""

import io
from urllib.parse import quote, unquote

from flask import render_template, request, send_file

from tiklocal.i18n import t
from tiklocal.services.radio import RadioCandidate
from tiklocal.web import read_int_arg
from tiklocal.web.media_payloads import media_urls


def register_radio_routes(app, library_service, radio_service, thumbnail_service, energy_service):
    def _positive_ratio(value) -> float | None:
        try:
            ratio = float(value)
        except (TypeError, ValueError):
            return None
        return max(0.0, min(ratio, 1.0))

    def _read_exclude_arg(name: str = 'exclude') -> set[str]:
        values: list[str] = []
        for raw in request.args.getlist(name):
            values.extend(str(raw or '').split(','))
        return {
            library_service.canonicalize_uri(value)
            for value in values
            if str(value or '').strip()
        }

    def _localize_station(station: dict) -> dict:
        """Translate a station's copy by id, keeping the service text as fallback."""
        station_id = str(station.get('id') or '')
        name_key = f'radio.station.{station_id}.name'
        desc_key = f'radio.station.{station_id}.desc'
        name, description = t(name_key), t(desc_key)
        return {
            **station,
            'name': station.get('name') if name == name_key else name,
            'description': station.get('description') if description == desc_key else description,
        }

    def _serialize_radio_track(item: RadioCandidate) -> dict:
        return {
            'name': item.name,
            **media_urls(item.name),
            'artwork_url': f'/api/radio/artwork?uri={quote(item.name, safe="")}',
            'energy_url': f'/api/radio/energy?uri={quote(item.name, safe="")}',
            'title': item.title,
            'artist': item.artist,
            'album': item.album,
            'duration': item.duration,
            'is_favorite': item.is_favorite,
        }

    @app.route('/radio')
    def radio_view():
        """Audio Radio Player"""
        return render_template('radio.html', menu='radio')

    @app.route('/api/radio/artwork')
    def api_radio_artwork():
        uri = library_service.find_existing_uri(unquote(request.args.get('uri') or ''))
        path, mimetype = thumbnail_service.get_radio_artwork(uri)
        return send_file(io.BytesIO(path) if isinstance(path, bytes) else path, mimetype=mimetype)

    @app.route('/api/radio/energy')
    def api_radio_energy():
        uri = library_service.find_existing_uri(unquote(request.args.get('uri') or ''))
        envelope = energy_service.get_envelope(uri) if uri else None
        if envelope is None:
            return {'success': False, 'error': t('radio.energy_unavailable')}, 404
        return {'success': True, 'data': {'hop': envelope['hop'], 'values': envelope['values']}}

    @app.route('/api/radio/items')
    def api_radio_items():
        offset = read_int_arg('offset', 0, minimum=0)
        limit = read_int_arg('limit', 200, minimum=1, maximum=500)
        audios = radio_service.collect_candidates()
        total = len(audios)
        page = audios[offset:offset + limit]
        items = []
        for track in page:
            metadata = radio_service.metadata_for(track.path)
            items.append({
                **_serialize_radio_track(track),
                'title': metadata.title or track.title,
                'artist': metadata.artist,
                'album': metadata.album,
                'duration': metadata.duration,
            })
        return {'success': True, 'data': {
            'items': items,
            'total': total,
            'has_more': offset + limit < total,
        }}

    @app.route('/api/radio/stations')
    def api_radio_stations():
        return {'success': True, 'data': {
            'stations': [_localize_station(station) for station in radio_service.list_stations()],
        }}

    @app.route('/api/radio/tune')
    def api_radio_tune():
        limit = read_int_arg('limit', 12, minimum=1, maximum=30)
        payload = radio_service.tune(
            station=str(request.args.get('station', 'default')).strip(),
            limit=limit,
            exclude=_read_exclude_arg(),
            seed=str(request.args.get('seed', '')).strip() or None,
            serialize_track=_serialize_radio_track,
        )
        if isinstance(payload.get('station'), dict):
            payload['station'] = _localize_station(payload['station'])
        return {'success': True, 'data': payload}

    @app.route('/api/radio/metadata')
    def api_radio_metadata():
        uri = library_service.find_existing_uri(unquote(request.args.get('uri') or ''))
        path = library_service.resolve_path(uri)
        if not uri or not path or not path.exists() or not path.is_file():
            return {'success': False, 'error': t('radio.audio_not_found')}, 404
        metadata = radio_service.metadata_for(path)
        return {'success': True, 'data': {
            'name': library_service.get_relative_path(path),
            'title': metadata.title or path.stem,
            'artist': metadata.artist,
            'album': metadata.album,
            'duration': metadata.duration,
        }}

    @app.route('/api/radio/feedback', methods=['POST'])
    def api_radio_feedback():
        payload = request.get_json(silent=True) or {}
        event = str(payload.get('event') or '').strip()
        uri = str(payload.get('name') or payload.get('uri') or '').strip()
        if event not in {'play', 'complete', 'replay', 'skip', 'favorite', 'error'}:
            return {'success': False, 'error': t('radio.invalid_feedback')}, 400
        ratio = _positive_ratio(payload.get('ratio'))
        entry = radio_service.record_feedback(uri, event, ratio=ratio)
        return {'success': True, 'data': {'profile': entry}}
