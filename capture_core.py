"""Uncompressed capture storage and export-only JPEG encoding."""
from __future__ import annotations
import io
import itertools
import os
from pathlib import Path
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable
from PIL import Image
MAX_PHOTOS = 15
MAX_JPEG_BYTES = 2_000_000
MAX_LONG_EDGE = 2560

@dataclass(frozen=True)
class Photo:
    """Encoded JPEG produced at export time, never stored as a capture."""
    jpeg: bytes
    width: int
    height: int
    quality: int


@dataclass(frozen=True, eq=False)
class CapturedPhoto:
    """Owned immutable RGB bytes plus a separate review thumbnail."""
    rgb: bytes = field(repr=False)
    width: int
    height: int
    thumbnail: Image.Image = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if (not isinstance(self.rgb, bytes) or self.width < 1 or self.height < 1
                or len(self.rgb) != self.width * self.height * 3):
            raise ValueError("A capture requires complete packed RGB pixels.")
        thumbnail = self.to_image()
        thumbnail.thumbnail((265, 150), Image.Resampling.LANCZOS)
        object.__setattr__(self, "thumbnail", thumbnail)

    @classmethod
    def from_image(cls, source: Image.Image) -> CapturedPhoto:
        image = source.convert("RGB")
        return cls(image.tobytes(), image.width, image.height)

    def to_image(self) -> Image.Image:
        return Image.frombytes("RGB", (self.width, self.height), self.rgb)


def compress_jpeg(source: Image.Image, *, max_bytes: int = MAX_JPEG_BYTES,
                  max_edge: int = MAX_LONG_EDGE) -> Photo:
    """Reduce quality, then dimensions, checking actual encoded byte length."""
    if max_bytes <= 0 or max_edge <= 0:
        raise ValueError("Image size limits must be positive.")
    image = source.convert("RGB")
    image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    while True:
        for quality in range(95, 59, -5):
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=quality, optimize=True,
                       subsampling=2)
            jpeg = buffer.getvalue()
            if len(jpeg) < max_bytes:
                return Photo(jpeg, image.width, image.height, quality)
        if image.size == (1, 1):
            raise ValueError("The byte limit is too small for a JPEG file.")
        smaller = (max(1, int(image.width * 0.85)),
                   max(1, int(image.height * 0.85)))
        image.thumbnail(smaller, Image.Resampling.LANCZOS)


def safe_label(label: str) -> str:
    """Make a short, portable filename component without accepting a path."""
    label = unicodedata.normalize("NFKC", label.strip())
    label = re.sub(r"[^\w .-]+", "_", label).strip(" ._")[:60]
    while len(label.encode("utf-8")) > 140:
        label = label[:-1]
    label = label.rstrip(" .")
    if not label:
        raise ValueError("Enter a label containing letters or numbers.")
    reserved = {"CON", "PRN", "AUX", "NUL"}
    reserved.update(f"{prefix}{number}" for prefix in ("COM", "LPT")
                    for number in range(1, 10))
    if label.split(".", 1)[0].upper() in reserved:
        label = "set_" + label
    return label


class CaptureSet:
    """Hold at most 15 uncompressed captures, unchanged by JPEG export."""

    def __init__(self) -> None:
        self.photos: list[CapturedPhoto] = []
        self.revision = 0
        self.exported_revision = 0

    @property
    def dirty(self) -> bool:
        return bool(self.photos) and self.revision != self.exported_revision

    def add(self, photo: CapturedPhoto) -> None:
        if len(self.photos) >= MAX_PHOTOS:
            raise ValueError("The set already contains 15 photos.")
        if not isinstance(photo, CapturedPhoto):
            raise ValueError("Store uncompressed captures; JPEG encoding happens at export.")
        self.photos.append(photo)
        self.revision += 1

    def remove(self, index: int) -> None:
        del self.photos[index]
        self.revision += 1

    def clear(self) -> None:
        self.photos.clear()
        self.revision = self.exported_revision = 0

    def mark_exported(self) -> None:
        self.exported_revision = self.revision


def export_photos(destination: Path, label: str,
                  photos: tuple[CapturedPhoto, ...],
                  progress: Callable[[int, int], None] | None = None) -> Path:
    """Write a new uniquely named folder, retaining the in-memory set on error."""
    label = safe_label(label)
    if not 1 <= len(photos) <= MAX_PHOTOS:
        raise ValueError("Export requires between 1 and 15 photos.")
    if any(not isinstance(photo, CapturedPhoto) for photo in photos):
        raise ValueError("Export requires uncompressed captures.")
    destination = Path(destination).expanduser().resolve(strict=True)
    if not destination.is_dir():
        raise ValueError("Choose an existing destination directory.")
    name = f"{label}_{datetime.now():%Y%m%d_%H%M%S}"
    for number in itertools.count(1):
        folder = destination / (name if number == 1 else f"{name}_{number}")
        try:
            folder.mkdir(mode=0o700)
            break
        except FileExistsError:
            continue
    try:
        for number, photo in enumerate(photos, 1):
            if progress is not None:
                progress(number, len(photos))
            source = photo.to_image()
            encoded = compress_jpeg(source)
            if not 0 < len(encoded.jpeg) < MAX_JPEG_BYTES:
                raise ValueError("A JPEG exceeds the export size limit.")
            filename = folder / f"{label}_{number:02d}.jpg"
            with filename.open("xb") as output:
                output.write(encoded.jpeg)
                output.flush()
                os.fsync(output.fileno())
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return folder
