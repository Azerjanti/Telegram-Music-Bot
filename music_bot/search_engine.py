from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s-]", " ", value.casefold())).strip()


def similarity(query: str, title: str, artist: str) -> float:
    normalized_query = normalize(query)
    candidates = (normalize(title), normalize(f"{artist} {title}"), normalize(f"{title} {artist}"))
    return max(fuzz.token_set_ratio(normalized_query, candidate) for candidate in candidates)


@dataclass(frozen=True)
class TrackMetadata:
    title: str
    artist: str
    source_url: str | None = None


def parse_track_title(raw_title: str, uploader: str | None = None) -> TrackMetadata:
    cleaned = re.sub(r"\s+", " ", raw_title).strip()
    artist = (uploader or "").strip()
    title = cleaned

    for separator in (" - ", " – ", " — ", " | "):
        if separator in cleaned:
            left, right = cleaned.split(separator, 1)
            if len(left.strip()) > 1 and len(right.strip()) > 1:
                artist = left.strip()
                title = right.strip()
                break

    title = re.sub(r"\s*\[(Official|Audio|Music Video|Lyrics?)\]\s*", "", title, flags=re.I).strip()
    return TrackMetadata(title=title[:250] or "Без названия", artist=artist[:250] or "Неизвестный исполнитель")