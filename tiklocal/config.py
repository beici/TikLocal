from tiklocal.services.library import normalize_source_id


def _coerce_source_list(raw_sources):
    sources = []
    if isinstance(raw_sources, dict):
        raw_sources = [{'id': key, 'path': value, 'name': key} for key, value in raw_sources.items()]
    if isinstance(raw_sources, list):
        for item in raw_sources:
            if not isinstance(item, dict):
                continue
            source_id = normalize_source_id(item.get('id') or item.get('name'))
            path = str(item.get('path') or '').strip()
            if not path:
                continue
            sources.append({
                'id': source_id,
                'name': str(item.get('name') or source_id).strip() or source_id,
                'path': path,
            })
    return sources


def normalize_media_sources(config, cli_sources=None, media_root=None):
    # CLI sources extend the configured list instead of replacing it: passing one
    # --media-source used to silently drop every source declared in config.yaml.
    sources = _coerce_source_list(config.get('media_sources') or [])
    from_cli = _coerce_source_list(cli_sources or [])
    if from_cli:
        positions = {item['id']: index for index, item in enumerate(sources)}
        for item in from_cli:
            if item['id'] in positions:
                sources[positions[item['id']]] = item
            else:
                positions[item['id']] = len(sources)
                sources.append(item)
    if media_root:
        # An explicit directory (CLI argument or MEDIA_ROOT) must win over a
        # 'default' source declared in config.yaml. Skipping the insert when such a
        # source existed meant `tiklocal /new/library` silently served the configured
        # library instead, so deletions and downloads landed in the wrong place.
        explicit = str(media_root)
        for index, item in enumerate(sources):
            if item.get('id') == 'default':
                sources[index] = {**item, 'path': explicit}
                break
        else:
            sources.insert(0, {'id': 'default', 'name': 'Default', 'path': explicit})
    return sources


def similarity_enabled(experiments=None, embedding=None) -> bool:
    """Resolve the startup switch without importing vector runtime code."""
    if experiments is not None and not isinstance(experiments, dict):
        raise ValueError('experiments must be a configuration object')
    similarity = (experiments or {}).get('similarity', {})
    if not isinstance(similarity, dict):
        raise ValueError('experiments.similarity must be a configuration object')
    if 'enabled' in similarity:
        if not isinstance(similarity['enabled'], bool):
            raise ValueError('experiments.similarity.enabled must be a boolean')
        return similarity['enabled']
    # Older configurations enabled the feature with embedding.enabled.
    return isinstance(embedding, dict) and embedding.get('enabled') is True
