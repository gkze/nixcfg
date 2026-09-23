"""Updater for Gemini for macOS releases."""

import json
import re
from typing import ClassVar

import aiohttp

from lib import json_utils
from lib.update.net import HTTP_BAD_REQUEST
from lib.update.updaters import (
    DownloadUrlMetadataUpdater,
    UpdateContext,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import DownloadUrlMetadata

_APP_ID = "com.google.geminimacos"
_CHANNEL = "m1-prod"
_ANTI_XSSI_PREFIX = b")]}'\n"
_EMPTY_VERSION = "0.0.0.0"  # Omaha sentinel, not a bind address.  # noqa: S104
_MAX_VERSION_COMPONENT = (1 << 32) - 1
_VERSION_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+){0,3}")
_DL_GOOGLE_URL_PREFIX = "https://dl.google.com/"


def _version_key(version: str) -> tuple[int, ...]:
    if _VERSION_PATTERN.fullmatch(version) is None:
        raise ValueError(version)
    components = tuple(int(component) for component in version.split("."))
    if any(component > _MAX_VERSION_COMPONENT for component in components):
        raise ValueError(version)
    return (*components, *(0 for _ in range(4 - len(components))))


def _effective_version_info(
    context: UpdateContext,
    upstream: VersionInfo,
) -> VersionInfo:
    current = context.current
    if (
        current is None
        or current.version is None
        or _version_key(upstream.version) >= _version_key(current.version)
    ):
        return upstream

    current_url = (current.urls or {}).get("aarch64-darwin")
    if not current_url:
        msg = "Cannot safely refresh a newer Gemini pin without its current DMG URL"
        raise RuntimeError(msg)
    return VersionInfo(
        version=current.version,
        metadata=DownloadUrlMetadata(url=current_url),
    )


def _response_updatecheck(payload_bytes: bytes) -> dict[str, object]:
    """Parse the Omaha payload and return the app's updatecheck mapping."""
    if not payload_bytes.startswith(_ANTI_XSSI_PREFIX):
        msg = "Gemini Omaha response omitted its anti-XSSI prefix"
        raise RuntimeError(msg)
    try:
        payload_value = json.loads(payload_bytes.removeprefix(_ANTI_XSSI_PREFIX))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        msg = "Gemini Omaha response was not valid JSON"
        raise RuntimeError(msg) from exc
    payload = json_utils.as_object_dict(
        payload_value,
        context="Gemini Omaha response",
    )
    response = json_utils.as_object_dict(
        payload.get("response"),
        context="Gemini Omaha response.response",
    )
    apps = json_utils.as_object_list(
        response.get("apps"),
        context="Gemini Omaha response.response.apps",
    )
    parsed_apps = [
        json_utils.as_object_dict(
            app,
            context="Gemini Omaha response app",
        )
        for app in apps
    ]
    matching_apps = [app for app in parsed_apps if app.get("appid") == _APP_ID]
    if len(matching_apps) != 1:
        msg = f"Gemini Omaha response contained {len(matching_apps)} matching apps"
        raise RuntimeError(msg)
    app = matching_apps[0]
    app_status = json_utils.get_required_str(
        app,
        "status",
        context="Gemini Omaha app",
    )
    if app_status != "ok":
        msg = f"Gemini Omaha app returned status {app_status!r}"
        raise RuntimeError(msg)
    return json_utils.as_object_dict(
        app.get("updatecheck"),
        context="Gemini Omaha updatecheck",
    )


def _inner_crx3_path(updatecheck: dict[str, object]) -> str:
    """Return the CRX3 operation's declared inner artifact path."""
    pipelines = json_utils.as_object_list(
        updatecheck.get("pipelines"),
        context="Gemini Omaha updatecheck.pipelines",
    )
    paths: list[str] = []
    for pipeline in pipelines:
        pipeline_obj = json_utils.as_object_dict(
            pipeline,
            context="Gemini Omaha pipeline",
        )
        operations = json_utils.as_object_list(
            pipeline_obj.get("operations"),
            context="Gemini Omaha pipeline.operations",
        )
        for operation in operations:
            operation_obj = json_utils.as_object_dict(
                operation,
                context="Gemini Omaha operation",
            )
            if operation_obj.get("type") != "crx3":
                continue
            path = operation_obj.get("path")
            if not isinstance(path, str):
                msg = "Gemini Omaha crx3 operation omitted its inner artifact path"
                raise TypeError(msg)
            paths.append(path)
    if len(paths) != 1:
        msg = f"Gemini Omaha payload declared {len(paths)} crx3 inner artifacts"
        raise RuntimeError(msg)
    return paths[0]


@register_updater
class GeminiUpdater(DownloadUrlMetadataUpdater):
    """Resolve Gemini releases from Google's Omaha update service."""

    name = "gemini"
    materialize_when_current = True
    supported_platforms = ("aarch64-darwin",)
    UPDATE_URL = "https://update.googleapis.com/service/update2/json"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
    }
    URL_METADATA_CONTEXT = "Gemini metadata"

    @staticmethod
    def _request_body() -> dict[str, object]:
        return {
            "request": {
                "@os": "mac",
                "@updater": "nixcfg",
                "acceptformat": "crx3,download,puff,run,xz,zucc",
                "apps": [
                    {
                        "ap": _CHANNEL,
                        "appid": _APP_ID,
                        "enabled": True,
                        "updatecheck": {},
                        "version": _EMPTY_VERSION,
                    }
                ],
                "arch": "arm64",
                "dedup": "cr",
                "domainjoined": False,
                "ismachine": False,
                "os": {
                    "arch": "arm64",
                    "platform": "Mac OS X",
                    "version": "15.0",
                },
                "prodversion": "0",
                "protocol": "4.0",
                "testsource": "nixcfg-updater",
                "updaterversion": "0",
            }
        }

    @classmethod
    def _parse_version(cls, payload_bytes: bytes) -> str:
        updatecheck = _response_updatecheck(payload_bytes)
        update_status = json_utils.get_required_str(
            updatecheck,
            "status",
            context="Gemini Omaha updatecheck",
        )
        if update_status != "ok":
            msg = f"Gemini Omaha updatecheck returned status {update_status!r}"
            raise RuntimeError(msg)
        version = json_utils.get_required_str(
            updatecheck,
            "nextversion",
            context="Gemini Omaha updatecheck",
        )
        try:
            _version_key(version)
        except ValueError as exc:
            msg = f"Gemini Omaha returned invalid version {version!r}"
            raise RuntimeError(msg) from exc

        return version

    @staticmethod
    def _parse_payload_urls(updatecheck: dict[str, object]) -> set[str]:
        """Return the official first-party download URLs in the payload."""
        pipelines = json_utils.as_object_list(
            updatecheck.get("pipelines"),
            context="Gemini Omaha updatecheck.pipelines",
        )
        urls: set[str] = set()
        for pipeline in pipelines:
            pipeline_obj = json_utils.as_object_dict(
                pipeline,
                context="Gemini Omaha pipeline",
            )
            operations = json_utils.as_object_list(
                pipeline_obj.get("operations"),
                context="Gemini Omaha pipeline.operations",
            )
            for operation in operations:
                operation_obj = json_utils.as_object_dict(
                    operation,
                    context="Gemini Omaha operation",
                )
                if operation_obj.get("type") != "download":
                    continue
                operation_urls = json_utils.as_object_list(
                    operation_obj.get("urls"),
                    context="Gemini Omaha download operation.urls",
                )
                for url_value in operation_urls:
                    raw_url = (
                        url_value.get("url")
                        if isinstance(url_value, dict)
                        else url_value
                    )
                    if not isinstance(raw_url, str):
                        msg = "Gemini Omaha download URL must be a string"
                        raise TypeError(msg)
                    if raw_url.startswith(_DL_GOOGLE_URL_PREFIX):
                        urls.add(raw_url)
        return urls

    @classmethod
    def _parse_download_url(cls, payload_bytes: bytes, *, version: str) -> str:
        """Return the pinned URL for the Omaha-served CRX3 container."""
        updatecheck = _response_updatecheck(payload_bytes)
        update_status = json_utils.get_required_str(
            updatecheck,
            "status",
            context="Gemini Omaha updatecheck",
        )
        if update_status != "ok":
            msg = f"Gemini Omaha updatecheck returned status {update_status!r}"
            raise RuntimeError(msg)
        urls = cls._parse_payload_urls(updatecheck)
        if len(urls) != 1:
            msg = f"Gemini Omaha payload contained {len(urls)} official download URLs"
            raise RuntimeError(msg)
        container_url = urls.pop()
        if not container_url.endswith(".crx3"):
            msg = f"Gemini Omaha download URL is not a CRX3 container: {container_url}"
            raise RuntimeError(msg)
        inner_path = _inner_crx3_path(updatecheck)
        # Mirrors mkCrx3DmgApp's default innerDmgName in
        # overlays/_lib/helpers/darwin-apps.nix:
        # "${capitalizedAppName}-${info.version}.dmg".
        # test_gemini_inner_dmg_name_contract_is_pinned_by_the_helper pins it.
        expected_inner = f"Gemini-{version}.dmg"
        if inner_path != expected_inner:
            msg = (
                "Gemini Omaha payload named inner artifact "
                f"{inner_path!r}, expected {expected_inner!r}"
            )
            raise RuntimeError(msg)
        return container_url

    async def _fetch_payload(self, session: aiohttp.ClientSession) -> bytes:
        headers = {
            "User-Agent": self.config.default_user_agent,
            "Content-Type": "application/json",
            "X-Goog-Update-Interactivity": "fg",
            "X-Goog-Update-AppId": _APP_ID,
            "X-Goog-Update-Updater": "nixcfg-0",
        }
        timeout = aiohttp.ClientTimeout(total=self.config.default_timeout)
        async with session.request(
            "POST",
            self.UPDATE_URL,
            headers=headers,
            json=self._request_body(),
            allow_redirects=True,
            timeout=timeout,
        ) as response:
            payload_bytes = await response.read()
            if response.status >= HTTP_BAD_REQUEST:
                msg = (
                    "Gemini Omaha request failed with "
                    f"HTTP {response.status} {response.reason}"
                )
                raise RuntimeError(msg)
        return payload_bytes

    async def _fetch_version(self, session: aiohttp.ClientSession) -> str:
        return self._parse_version(await self._fetch_payload(session))

    async def fetch_latest(
        self,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext,
    ) -> VersionInfo:
        """Resolve one release from Google's Omaha update service."""
        payload_bytes = await self._fetch_payload(session)
        version = self._parse_version(payload_bytes)
        return _effective_version_info(
            context,
            VersionInfo(
                version=version,
                metadata=DownloadUrlMetadata(
                    url=self._parse_download_url(payload_bytes, version=version),
                ),
            ),
        )
