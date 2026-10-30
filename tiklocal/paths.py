import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

#: Scratch folder a download builds in before it publishes files into the library.
#: It lives under the media root so the final move is a rename, and the scanner
#: skips it so half-written files never enter the index.
DOWNLOAD_TMP_DIR = '.tiklocal-download-tmp'

_relative_instance_warned = False


def get_data_dir() -> Path:
    """Return the global data directory for TikLocal.
    Default to ~/.tiklocal unless TIKLOCAL_INSTANCE is set.
    """
    global _relative_instance_warned
    base = os.environ.get('TIKLOCAL_INSTANCE')
    if base:
        p = Path(base).expanduser()
        if not p.is_absolute():
            # A relative instance path resolved against whatever directory the
            # process happened to start in, so the same command line could read a
            # different database after a `cd`. Make the resolution explicit and
            # say so once instead of silently writing next to the CWD.
            p = (Path.cwd() / p).resolve()
            if not _relative_instance_warned:
                _relative_instance_warned = True
                logger.warning(
                    'TIKLOCAL_INSTANCE is relative; resolved to %s (the current working directory)',
                    p,
                )
    else:
        p = Path.home() / '.tiklocal'
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_thumbnails_dir() -> Path:
    d = get_data_dir() / 'thumbnails'
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_thumbs_map_path() -> Path:
    return get_data_dir() / 'thumbs.json'


def get_metadata_path() -> Path:
    return get_data_dir() / 'metadata.json'


def get_favorites_path() -> Path:
    return get_data_dir() / 'favorites.json'


def get_prompt_config_path() -> Path:
    return get_data_dir() / 'prompt_config.json'


def get_llm_config_path() -> Path:
    return get_data_dir() / 'llm_config.json'


def get_database_path() -> Path:
    return get_data_dir() / 'tiklocal.sqlite3'


def get_download_config_path() -> Path:
    return get_data_dir() / 'download_config.json'


def get_download_jobs_path() -> Path:
    return get_data_dir() / 'download_jobs.json'


def get_download_sources_path() -> Path:
    return get_data_dir() / 'download_sources.json'


def get_media_sources_path() -> Path:
    # Library folders managed from the settings page; separate from the download
    # source map above, which records which blogger a downloaded file came from.
    return get_data_dir() / 'media_sources.json'


def get_download_urls_path() -> Path:
    return get_data_dir() / 'download-urls.txt'


def get_download_urls_config_path() -> Path:
    return get_data_dir() / 'download-urls.json'


def get_collections_path() -> Path:
    return get_data_dir() / 'collections.json'


def get_radio_profile_path() -> Path:
    return get_data_dir() / 'radio_profile.json'


def get_author_links_config_path() -> Path:
    """Where the last used author-link dump path is remembered.

    The dump lives on the machine that runs TikTokDownloader, so the reader
    retypes a long path every time it is not remembered: one small file with one
    key beats a new table in the library database for a setting this small.
    """
    return get_data_dir() / 'author_links.json'


def get_auth_path() -> Path:
    return get_data_dir() / 'auth.json'


def get_device_auth_path() -> Path:
    return get_data_dir() / 'radio_devices.json'


def is_in_data_dir(path) -> bool:
    """True when *path* is the data directory or lives inside it.

    The data directory is not media: `auth.json` holds the password hash and the
    Flask `secret_key`, `radio_devices.json` holds every paired device's bearer
    token, and the TLS private key and cookie jar sit next to them. A media source
    that covers this directory would hand all of that out through /media, which is
    enough to sign a session cookie and stay logged in after a password change.
    """
    try:
        candidate = Path(path).expanduser().resolve()
        data_dir = get_data_dir().resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    return candidate == data_dir or data_dir in candidate.parents

