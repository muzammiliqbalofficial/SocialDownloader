"""The platform capability registry (section 4).

Data-driven on purpose: platform support is a table, not a pile of `if`
statements, and the frontend renders its tabs from what this module reports.

Adding a platform means adding a module with a `SPEC` and listing it in
`_ALL_SPECS`. Nothing else in the codebase should branch on platform identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from app.config import Settings, get_settings
from app.core.errors import AppError, ErrorCode
from app.platforms import facebook, instagram, linkedin, snapchat, youtube
from app.platforms.base import (
    CapabilityInfo,
    ContentType,
    Platform,
    PlatformSpec,
)

_ALL_SPECS: tuple[PlatformSpec, ...] = (
    youtube.SPEC,
    instagram.SPEC,
    facebook.SPEC,
    linkedin.SPEC,
    snapchat.SPEC,
)

REGISTRY: dict[Platform, PlatformSpec] = {spec.platform: spec for spec in _ALL_SPECS}

# Hostname -> platform, including the bare domain and any subdomain of it.
_HOST_INDEX: dict[str, Platform] = {
    host: spec.platform for spec in _ALL_SPECS for host in spec.hosts
}

# Schemes we will even consider. Anything else is a malformed paste at best and
# an SSRF attempt at worst (file://, gopher://, data:).
_ALLOWED_SCHEMES = frozenset({"http", "https"})


@dataclass(frozen=True)
class Detection:
    platform: Platform
    content_type: ContentType
    spec: PlatformSpec
    # The platform's own id for the content, where the URL exposes one.
    content_id: str | None
    capabilities: tuple[CapabilityInfo, ...]


def _normalise_host(netloc: str) -> str:
    host = netloc.split("@")[-1].split(":")[0].lower().strip()
    return host[4:] if host.startswith("www.") else host


def platform_for_host(host: str) -> Platform | None:
    """Exact match first, then walk up the domain so any subdomain resolves."""
    normalised = _normalise_host(host)
    if normalised in _HOST_INDEX:
        return _HOST_INDEX[normalised]

    labels = normalised.split(".")
    for index in range(1, len(labels) - 1):
        candidate = ".".join(labels[index:])
        if candidate in _HOST_INDEX:
            return _HOST_INDEX[candidate]
    return None


def is_enabled(spec: PlatformSpec, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    if spec.platform is Platform.SNAPCHAT:
        return settings.snapchat_enabled
    return spec.default_enabled


def enabled_specs(settings: Settings | None = None) -> tuple[PlatformSpec, ...]:
    """What the UI is allowed to know about.

    A disabled platform is absent entirely rather than present-and-greyed-out
    (decision D-004).
    """
    settings = settings or get_settings()
    return tuple(spec for spec in _ALL_SPECS if is_enabled(spec, settings))


def supported_platform_names(settings: Settings | None = None) -> list[str]:
    """Display names of platforms that are enabled *and* actually working.

    Used in error messages so we never advertise a platform whose phase has
    not landed or that ships disabled.
    """
    settings = settings or get_settings()
    return [spec.display_name for spec in enabled_specs(settings) if spec.implemented]


def _supported_detail(settings: Settings) -> str:
    names = supported_platform_names(settings)
    if not names:  # pragma: no cover - only if everything is switched off
        return "No platforms are currently available."
    if len(names) == 1:
        return f"Right now we support {names[0]}."
    return f"Right now we support {', '.join(names[:-1])} and {names[-1]}."


def detect(url: str, settings: Settings | None = None) -> Detection:
    """Resolve a pasted URL to a platform and content type.

    Raises `AppError` with a specific taxonomy code rather than returning None,
    so every rejection reaches the user as an actionable message.
    """
    settings = settings or get_settings()
    candidate = (url or "").strip()

    if not candidate:
        raise AppError(ErrorCode.INVALID_URL, detail="No URL was provided.")

    # Bare "youtube.com/watch?v=..." pastes are common; assume https.
    if "://" not in candidate:
        candidate = f"https://{candidate}"

    try:
        parts = urlsplit(candidate)
    except ValueError as exc:
        raise AppError(ErrorCode.INVALID_URL, cause=exc) from exc

    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise AppError(
            ErrorCode.INVALID_URL,
            detail="Only http and https links are supported.",
        )

    if not parts.netloc:
        raise AppError(ErrorCode.INVALID_URL, detail="That URL has no domain.")

    platform = platform_for_host(parts.netloc)
    if platform is None:
        raise AppError(
            ErrorCode.UNSUPPORTED_PLATFORM,
            detail=_supported_detail(settings),
            context={"host_known": False},
        )

    spec = REGISTRY[platform]

    if not is_enabled(spec, settings):
        # A disabled platform is indistinguishable from an unsupported one, by
        # design: the UI never hints at something it cannot deliver.
        raise AppError(
            ErrorCode.UNSUPPORTED_PLATFORM,
            detail=_supported_detail(settings),
            context={"platform": str(platform)},
        )

    matched = spec.match(candidate)
    if matched is None:
        raise AppError(
            ErrorCode.UNSUPPORTED_CONTENT_TYPE,
            # Phrased without an article: "a Instagram" was the alternative.
            detail=(
                f"We recognise {spec.display_name} links, but not that one. "
                "Paste a link to a specific video or post."
            ),
            context={"platform": str(platform)},
        )

    content_type, content_id = matched
    capabilities = spec.capabilities_for(content_type)

    if not capabilities:
        raise AppError(
            ErrorCode.UNSUPPORTED_CONTENT_TYPE,
            detail=(
                f"{spec.display_name} {content_type.value}s aren't supported. "
                "Paste a link to a single video or post."
            ),
            context={"platform": str(platform), "content_type": str(content_type)},
        )

    if not spec.implemented:
        raise AppError(
            ErrorCode.UNSUPPORTED_PLATFORM,
            detail=(
                f"{spec.display_name} support is still being built. "
                f"{_supported_detail(settings)}"
            ),
            context={"platform": str(platform)},
        )

    return Detection(
        platform=platform,
        content_type=content_type,
        spec=spec,
        content_id=content_id,
        capabilities=capabilities,
    )


def describe_registry(settings: Settings | None = None) -> list[dict]:
    """Serialisable capability matrix for the frontend."""
    settings = settings or get_settings()
    described: list[dict] = []

    for spec in enabled_specs(settings):
        described.append(
            {
                "platform": str(spec.platform),
                "display_name": spec.display_name,
                "implemented": spec.implemented,
                "notes": spec.notes,
                "known_limitations": list(spec.known_limitations),
                "content_types": {
                    str(content_type): [
                        {
                            "capability": str(info.capability),
                            "support": str(info.support),
                            "note": info.note,
                        }
                        for info in infos
                    ]
                    for content_type, infos in spec.capabilities.items()
                },
            }
        )
    return described
