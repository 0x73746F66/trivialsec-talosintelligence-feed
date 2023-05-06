# pylint: disable=no-self-argument, arguments-differ
import contextlib
import logging
import threading
import json
import errno
from time import sleep
from pathlib import Path
from uuid import UUID
from os import path, getenv
from socket import error as SocketError
from typing import Union
from base64 import urlsafe_b64encode
from datetime import datetime, date
from ipaddress import (
    IPv4Address,
    IPv6Address,
    IPv4Network,
    IPv6Network,
)

import boto3
import requests
from lumigo_tracer import add_execution_tag, report_error
from retry.api import retry
from pydantic import (
    HttpUrl,
    AnyHttpUrl,
    PositiveInt,
    PositiveFloat,
)


TALOS_NAMESPACE = UUID('623977ce-d10c-4b12-b75b-5376135241ef')
DEFAULT_LOG_LEVEL = logging.WARNING
LOG_LEVEL = getenv("LOG_LEVEL", 'WARNING')
CACHE_DIR = getenv("CACHE_DIR", "/tmp")
BUILD_ENV = getenv("BUILD_ENV", "development")
JITTER_SECONDS = int(getenv("JITTER_SECONDS", default="30"))
APP_ENV = getenv("APP_ENV", "Dev")
APP_NAME = getenv("APP_NAME", "feed-processor-talos-intelligence")
DASHBOARD_URL = "https://www.trivialsec.com"
logger = logging.getLogger(__name__)
if getenv("AWS_EXECUTION_ENV") is not None:
    boto3.set_stream_logger('boto3', getattr(logging, LOG_LEVEL, DEFAULT_LOG_LEVEL))
logger.setLevel(getattr(logging, LOG_LEVEL, DEFAULT_LOG_LEVEL))
logging.getLogger('urllib3').setLevel(logging.ERROR)


class DelayRetryHandler(Exception):
    """
    Delay the retry handler and provide a useful message when retries are exceeded
    """
    def __init__(self, **kwargs):
        sleep(kwargs.get("delay", 3) or 3)
        Exception.__init__(self, kwargs.get("msg", "Max retries exceeded"))


class UnspecifiedError(Exception):
    """
    The exception class for exceptions that weren't previously known.
    """
    def __init__(self, **kwargs):
        Exception.__init__(self, kwargs.get("msg", "An unspecified error occurred"))


class JSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, date):
            return o.isoformat()
        if isinstance(o, datetime):
            return o.replace(microsecond=0).isoformat()
        if isinstance(o, int) and o > 10 ^ 38 - 1:
            return str(o)
        if isinstance(
            o,
            (
                PositiveInt,
                PositiveFloat,
            ),
        ):
            return int(o)
        if isinstance(
            o,
            (
                HttpUrl,
                AnyHttpUrl,
                IPv4Address,
                IPv6Address,
                IPv4Network,
                IPv6Network,
                UUID,
            ),
        ):
            return str(o)
        if hasattr(o, "dict"):
            return json.dumps(o.dict(), cls=JSONEncoder)

        return super().default(o)


def _request_task(url, body, headers):
    with contextlib.suppress(requests.exceptions.ConnectionError):
        requests.post(url, data=json.dumps(body, cls=JSONEncoder), headers=headers, timeout=(15, 30))


def post_beacon(url: HttpUrl, body: dict, headers: dict = None):
    """
    A beacon is a fire and forget HTTP POST, the response is not
    needed so we do not even wait for one, so there is no
    response to discard because it was never received
    """
    if headers is None:
        headers = {"Content-Type": "application/json"}
    threading.Thread(target=_request_task, args=(url, body, headers)).start()


def trace_tag(data: dict[str, str]):
    if not isinstance(data, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in data.items()
    ):
        raise ValueError(data)
    for key, value in data.items():
        if len(key) > 50:
            logger.warning(f"Trace key must be less than 50 for: {value} See: https://docs.lumigo.io/docs/execution-tags#execution-tags-naming-limits-and-requirements")
        if len(value) > 70:
            logger.warning(f"Trace value must be less than 70 for: {value} See: https://docs.lumigo.io/docs/execution-tags#execution-tags-naming-limits-and-requirements")
    if getenv("AWS_EXECUTION_ENV") is None or APP_ENV != "Prod":
        return
    for key, value in data.items():
        add_execution_tag(key[:50], value=value[:70])


@retry((SocketError), tries=5, delay=1.5, backoff=1)
def download_file(remote_file: str, temp_dir: str = CACHE_DIR) -> Union[Path, None]:
    session = requests.Session()
    remote_file = remote_file.replace(":80/", "/").replace(":443/", "/")
    logger.info(f"[bold]Checking freshness[/bold] {remote_file}")
    resp = session.head(
        remote_file,
        verify=remote_file.startswith('https'),
        allow_redirects=True,
        timeout=5,
        headers={'User-Agent': "Trivial Security"}
    )
    if not str(resp.status_code).startswith('2'):
        if resp.status_code == 403:
            logger.warning(f"Forbidden {remote_file}")
        elif resp.status_code == 404:
            logger.warning(f"Not Found {remote_file}")
            return
        else:
            report_error(f"Unexpected HTTP response code {resp.status_code} for URL {remote_file}")
            return

    file_size = int(resp.headers.get('Content-Length', 0))
    dest_file = None
    if 'Content-disposition' in resp.headers:
        dest_file = resp.headers['Content-disposition'].replace('attachment;filename=', '').replace('attachment; filename=', '').replace('"', '', 2)
    if not dest_file:
        dest_file = urlsafe_b64encode(remote_file.encode('ascii')).decode('utf8').strip('=') + '.txt'

    temp_path = f'{temp_dir}/{dest_file}'
    logger.debug(f"[bold]temp_path[/bold] {temp_path}")
    etag_path = f'{temp_path}.etag'
    if file_size > 0 and path.exists(temp_path):
        local_size = 0
        try:
            local_size = path.getsize(temp_path)
        except OSError as err:
            if err.errno != errno.ENOENT:
                raise
        if local_size == file_size:
            logger.info(f"[bold]Not Modified[/bold] {temp_path}")
            return Path(temp_path)

    etag = resp.headers.get('ETag')
    if etag:
        local_etag = None
        if path.exists(etag_path):
            local_etag = Path(etag_path).read_text(encoding='utf8')
        if local_etag == etag:
            logger.info(f"[bold]Cached[/bold] {temp_path}")
            return Path(temp_path)

    logger.info(f"[bold]Downloading[/bold] {remote_file}")
    resp = session.get(
        remote_file,
        verify=remote_file.startswith('https'),
        allow_redirects=True,
        headers={'User-Agent': "Trivial Security"}
    )
    handle = Path(temp_path)
    handle.write_text(resp.text, encoding='utf8')
    if etag:
        logger.debug(f"[bold]etag[/bold] {etag}")
        Path(etag_path).write_text(etag, encoding='utf8')

    return Path(temp_path)
