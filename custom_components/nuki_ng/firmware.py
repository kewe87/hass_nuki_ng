"""Newest firmware per Nuki device, from the release notes of the Nuki help center.

Nuki has no API that tells the newest firmware of a device. The help center
(a Zendesk site) publishes one article per release, titled like
"Release Notes: Nuki Smart Lock 4.11.4", and offers a public search API.
The major version tells the generation: Smart Lock 2.x is the Smart Lock 2.0,
4.x the 4th generation, 5.x Ultra/Pro/Go. So the newest release with the same
major version as the installed one is the update for this device.

No Home Assistant imports, so the parsing can be tested on its own.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

SEARCH_URL = "https://help.nuki.io/api/v2/help_center/articles/search.json"
SEARCH_QUERY = "Release Notes"
MAX_PAGES = 5
SUMMARY_LENGTH = 255  # limit of the update entity

PRODUCT_BY_DEVICE_TYPE = {0: "Smart Lock", 2: "Opener", 3: "Smart Door", 4: "Smart Lock", 5: "Smart Lock"}

_TITLE = re.compile(
    r"^\s*Release Notes:?\s*(?:Nuki\s+)?(Smart Lock|Opener|Smart Door)\s+(\d+(?:\.\d+)+)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Release:
    product: str
    version: str
    url: str
    notes: str  # plain text, lines starting with "- " for list items
    published: str

    @property
    def key(self) -> tuple[int, ...]:
        return version_key(self.version)

    @property
    def summary(self) -> str:
        text = " ".join(line.lstrip("- ").strip() for line in self.notes.splitlines() if line.strip())
        return text if len(text) <= SUMMARY_LENGTH else text[: SUMMARY_LENGTH - 1].rsplit(" ", 1)[0] + "…"


def version_key(version: str | None) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version or ""))


def html_to_text(body: str | None) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", body or "")
    text = re.sub(r"(?i)<li[^>]*>", "\n- ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</h\d>|</li>|</ul>|</ol>", "\n", text)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    lines = [re.sub(r"[ \t\xa0]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line and line != "-")


def parse_articles(articles: list[dict]) -> list[Release]:
    releases = []
    for article in articles:
        if not (match := _TITLE.match(article.get("title") or "")):
            continue
        product = next(p for p in set(PRODUCT_BY_DEVICE_TYPE.values()) if p.lower() == match.group(1).lower())
        releases.append(Release(
            product, match.group(2), article.get("html_url") or "",
            html_to_text(article.get("body")), (article.get("created_at") or "")[:10],
        ))
    return releases


def newest(releases: list[Release], device_type: int | None, installed: str | None) -> Release | None:
    """Newest release for this device: same product and major version as installed."""
    product = PRODUCT_BY_DEVICE_TYPE.get(device_type)
    current = version_key(installed)
    if not product or not current:
        return None
    same = [r for r in releases if r.product == product and r.key[:1] == current[:1]]
    return max(same, key=lambda r: r.key, default=None)


def localized_url(url: str, language: str) -> str:
    """Link the article in the language of Home Assistant where the help center has it."""
    return re.sub(r"/hc/[a-z]{2}(?:-[0-9a-z]+)?/", f"/hc/{language}/", url, count=1) if language == "de" else url
