# pylint: disable=no-self-argument, arguments-differ
import contextlib
import re
import logging
import hmac
import hashlib
import threading
import json
import errno
from pathlib import Path
from uuid import UUID
from os import path, getenv
from socket import error as SocketError
from typing import Union
from base64 import b64encode, urlsafe_b64encode
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from ipaddress import (
    IPv4Address,
    IPv6Address,
    IPv4Network,
    IPv6Network,
)

import boto3
import requests
from lumigo_tracer import add_execution_tag
from retry.api import retry
from pydantic import (
    HttpUrl,
    AnyHttpUrl,
    PositiveInt,
    PositiveFloat,
    EmailStr,
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


def parse_authorization_header(authorization_header: str) -> dict[str, str]:
    auth_param_re = r'([a-zA-Z0-9_\-]+)=(([a-zA-Z0-9_\-]+)|("")|(".*[^\\]"))'
    auth_param_re = re.compile(r"^\s*" + auth_param_re + r"\s*$")
    unesc_quote_re = re.compile(r'(^")|([^\\]")')
    scheme, pairs_str = authorization_header.split(None, 1)
    parsed_header = {"scheme": scheme}
    pairs = []
    if pairs_str:
        for pair in pairs_str.split(","):
            if not pairs or auth_param_re.match(pairs[-1]):  # type: ignore
                pairs.append(pair)
            else:
                pairs[-1] = f"{pairs[-1]},{pair}"
        if not auth_param_re.match(pairs[-1]):  # type: ignore
            raise ValueError("Malformed auth parameters")
    for pair in pairs:
        (key, value) = pair.strip().split("=", 1)
        # For quoted strings, remove quotes and backslash-escapes.
        if value.startswith('"'):
            value = value[1:-1]
            if unesc_quote_re.search(value):
                raise ValueError("Unescaped quote in quoted-string")
            value = re.compile(r"\\.").sub(lambda m: m.group(0)[1], value)
        parsed_header[key] = value
    return parsed_header


class HMAC:
    default_algorithm = "sha512"
    supported_algorithms = {
        "sha256": hashlib.sha256,
        "sha384": hashlib.sha384,
        "sha512": hashlib.sha512,
        "sha3_256": hashlib.sha3_256,
        "sha3_384": hashlib.sha3_384,
        "sha3_512": hashlib.sha3_512,
        "blake2b512": hashlib.blake2b,
    }
    _not_before_seconds: int = JITTER_SECONDS
    _expire_after_seconds: int = JITTER_SECONDS

    @property
    def scheme(self) -> Union[str, None]:
        return (
            None
            if not hasattr(self, "parsed_header")
            else self.parsed_header.get("scheme")
        )

    @property
    def id(self) -> Union[str, None]:
        return (
            None if not hasattr(self, "parsed_header") else self.parsed_header.get("id")
        )

    @property
    def ts(self) -> Union[int, None]:
        return None if not hasattr(self, "parsed_header") else int(self.parsed_header.get("ts"))  # type: ignore

    @property
    def mac(self) -> Union[str, None]:
        return (
            self.parsed_header.get("mac")
            if hasattr(self, "parsed_header")
            else None
        )

    @property
    def canonical_string(self) -> str:
        parsed_url = urlparse(self.request_url)
        port = 443 if parsed_url.port is None else parsed_url.port
        bits = [self.request_method.upper()]
        bits.extend(
            (parsed_url.hostname.lower(), str(port), parsed_url.path, str(self.ts))
        )
        if self.contents:
            bits.append(b64encode(self.contents.encode("utf8")).decode("utf8"))
        return "\n".join(bits)

    def __init__(
        self,
        authorization_header: str,
        request_url: str,
        method: str = "GET",
        raw_body: Union[str, None] = None,  # type: ignore
        algorithm: Union[str, None] = None,  # type: ignore
        not_before_seconds: int = JITTER_SECONDS,
        expire_after_seconds: int = JITTER_SECONDS,
    ):
        self.server_mac: str = ""
        self.authorization_header: str = authorization_header
        self.contents = raw_body
        self.request_method: str = method
        self.request_url: str = request_url
        self.algorithm: str = (
            algorithm
            if self.supported_algorithms.get(algorithm)
            else self.default_algorithm
        )
        self._expire_after_seconds: int = expire_after_seconds
        self._not_before_seconds: int = not_before_seconds
        self.parsed_header: dict[str, str] = parse_authorization_header(
            authorization_header
        )

    def is_valid_scheme(self) -> bool:
        return self.authorization_header.startswith("HMAC")

    def is_valid_timestamp(self) -> bool:
        # not_before prevents replay attacks
        compare_date = datetime.fromtimestamp(float(self.ts), tz=timezone.utc)  # type: ignore
        now = datetime.now(tz=timezone.utc)
        not_before = now - timedelta(seconds=self._not_before_seconds)
        expire_after = now + timedelta(seconds=self._expire_after_seconds)
        # expire_after can assist with support for offline/aeroplane mode
        if compare_date < not_before or compare_date > expire_after:
            logger.info(
                f"now {now} compare_date {compare_date} not_before {not_before} expire_after {expire_after}"
            )
            logger.info(
                f"compare_date < not_before {compare_date < not_before} compare_date > expire_after {compare_date > expire_after}"
            )
            return False
        return True

    @staticmethod
    def _compare(*values):
        """
        _compare() takes two or more str or byte-like inputs and compares
        each to return True if they match or False if there is any mismatch
        """
        # In Python 3, if we have a bytes object, iterating it will already get the integer value
        def chk_bytes(val):
            return ord(
                val if isinstance(val, (bytes, bytearray)) else val.encode("utf8")
            )

        result = 0
        for index, this in enumerate(values):
            if index == 0:  # first index has nothing to compare
                continue
            # use the index variable i to locate prev
            prev = values[index - 1]
            # Constant time string comparison, mitigates side channel attacks.
            if len(prev) != len(this):
                return False
            for _x, _y in zip(chk_bytes(prev), chk_bytes(this)):  # type: ignore
                result |= _x ^ _y
        return result == 0

    def validate(self, secret_key: str):
        if not self.is_valid_scheme():
            logger.error(
                'incompatible authorization scheme, expected "Authorization: HMAC ..."'
            )
            return False
        if not self.is_valid_timestamp():
            logger.error(f"jitter detected {self.ts}")
            return False
        if not self.supported_algorithms.get(self.algorithm):  # type: ignore
            logger.error(f"algorithm {self.algorithm} is not supported")
            return False

        digestmod = self.supported_algorithms.get(self.algorithm, self.default_algorithm)  # type: ignore
        # Sign HMAC using server-side secret (not provided by client)
        digest = hmac.new(
            secret_key.encode("utf8"), self.canonical_string.encode("utf8"), digestmod
        ).hexdigest()  # type: ignore
        self.server_mac = digest
        # Compare server-side HMAC with client provided HMAC
        if invalid := not hmac.compare_digest(digest, self.mac):  # type: ignore
            logger.error(
                f"server_mac {self.server_mac} canonical_string {self.canonical_string}"
            )
        return not invalid


class JSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, datetime):
            return o.replace(microsecond=0).isoformat()
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
                AnyHttpUrl,
                IPv4Address,
                IPv6Address,
                IPv4Network,
                IPv6Network,
                UUID,
                EmailStr,
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
            logger.error(f"Unexpected HTTP response code {resp.status_code} for URL {remote_file}")
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
