from pydantic import BaseModel
from typing import Optional


class Chapter(BaseModel):
    title: Optional[str] = None
    start_absolute: Optional[float] = None
    end_absolute: Optional[float] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None


class AudiobookMetadata(BaseModel):
    title: Optional[str] = None
    author: Optional[str] = None
    duration: Optional[float] = None
    size: Optional[int] = None
    bitrate: Optional[int] = None
    duration_formatted: Optional[str] = None
    size_formatted: Optional[str] = None

    chapters: Optional[list[Chapter]] = None
    album_art: Optional[str] = None
    raw_metadata: Optional[dict] = None


class ActivationBytes(BaseModel):
    checksum: Optional[str] = None
    activation_bytes: Optional[str] = None
