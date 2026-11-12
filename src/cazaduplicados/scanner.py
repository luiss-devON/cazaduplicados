"""Búsqueda eficiente de archivos duplicados.

Estrategia en tres fases para leer lo mínimo posible del disco:

1. Agrupar por tamaño (solo metadatos, sin leer contenido).
2. Entre archivos del mismo tamaño, comparar un hash de los primeros 64 KB.
3. Solo a los que siguen empatados se les calcula el hash completo (BLAKE2b).
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator

CHUNK_SIZE = 1024 * 1024
PARTIAL_BYTES = 64 * 1024
QUARANTINE_MARKER = ".cazaduplicados-cuarentena"
DEFAULT_EXCLUDE = frozenset({".git", ".hg", ".svn", "__pycache__", "$RECYCLE.BIN", "System Volume Information"})


@dataclass(frozen=True)
class DuplicateGroup:
    """Conjunto de archivos con contenido idéntico."""

    size: int
    digest: str
    paths: tuple[Path, ...]

    @property
    def wasted(self) -> int:
        """Bytes que se recuperarían dejando una sola copia."""
        return self.size * (len(self.paths) - 1)

    def to_dict(self) -> dict:
        return {
            "tamano": self.size,
            "hash": self.digest,
            "recuperable": self.wasted,
            "archivos": [str(p) for p in self.paths],
        }


@dataclass
class ScanResult:
    groups: list[DuplicateGroup] = field(default_factory=list)
    files_scanned: int = 0
    bytes_scanned: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def wasted_total(self) -> int:
        return sum(g.wasted for g in self.groups)

    @property
    def duplicate_files(self) -> int:
        return sum(len(g.paths) - 1 for g in self.groups)

    def to_dict(self) -> dict:
        return {
            "archivos_analizados": self.files_scanned,
            "bytes_analizados": self.bytes_scanned,
            "grupos": len(self.groups),
            "archivos_sobrantes": self.duplicate_files,
            "recuperable_total": self.wasted_total,
            "errores": list(self.errors),
            "duplicados": [g.to_dict() for g in self.groups],
        }


def hash_file(path: Path, limit: int | None = None) -> str:
    """Hash BLAKE2b del archivo completo o de sus primeros `limit` bytes."""
    hasher = hashlib.blake2b(digest_size=20)
    remaining = limit
    with open(path, "rb") as fh:
        while remaining is None or remaining > 0:
            to_read = CHUNK_SIZE if remaining is None else min(CHUNK_SIZE, remaining)
            chunk = fh.read(to_read)
            if not chunk:
                break
            hasher.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
    return hasher.hexdigest()


def iter_files(
    roots: Iterable[str | os.PathLike],
    *,
    min_size: int = 1,
    exclude: Iterable[str] = DEFAULT_EXCLUDE,
    exclude_paths: Iterable[str | os.PathLike] = (),
    errors: list[str] | None = None,
) -> Iterator[tuple[Path, int]]:
    """Recorre las carpetas y devuelve (ruta, tamaño) de cada archivo regular.

    - No sigue enlaces simbólicos.
    - Un mismo archivo físico (rutas solapadas o enlaces duros) se cuenta una vez.
    """
    exclude_names = {name.lower() for name in exclude}
    blocked = {_safe_resolve(Path(p)) for p in exclude_paths}
    seen: set = set()
    errors = errors if errors is not None else []

    def candidate(path: Path) -> tuple[Path, int] | None:
        try:
            st = os.lstat(path)
        except OSError as exc:
            errors.append(f"{path}: {exc.strerror or exc}")
            return None
        if not stat.S_ISREG(st.st_mode) or st.st_size < min_size:
            return None
        key = (st.st_dev, st.st_ino) if st.st_ino else str(_safe_resolve(path))
        if key in seen:
            return None
        seen.add(key)
        return path, st.st_size

    def on_error(exc: OSError) -> None:
        errors.append(f"{exc.filename}: {exc.strerror or exc}")

    for root in roots:
        root = Path(root)
        if root.is_file():
            item = candidate(root)
            if item:
                yield item
            continue
        for dirpath, dirnames, filenames in os.walk(root, onerror=on_error):
            current = Path(dirpath)
            if QUARANTINE_MARKER in filenames:
                dirnames.clear()  # carpeta de cuarentena: nunca se analiza
                continue
            dirnames[:] = sorted(
                d
                for d in dirnames
                if d.lower() not in exclude_names and _safe_resolve(current / d) not in blocked
            )
            for name in sorted(filenames):
                item = candidate(current / name)
                if item:
                    yield item


def find_duplicates(
    roots: Iterable[str | os.PathLike],
    *,
    min_size: int = 1,
    exclude: Iterable[str] = DEFAULT_EXCLUDE,
    exclude_paths: Iterable[str | os.PathLike] = (),
    on_progress: Callable[[str, int, int], None] | None = None,
) -> ScanResult:
    """Busca duplicados en las rutas indicadas y los ordena por espacio recuperable."""
    result = ScanResult()
    by_size: dict[int, list[Path]] = defaultdict(list)

    for path, size in iter_files(
        roots, min_size=min_size, exclude=exclude, exclude_paths=exclude_paths, errors=result.errors
    ):
        by_size[size].append(path)
        result.files_scanned += 1
        result.bytes_scanned += size

    candidates = [(size, paths) for size, paths in by_size.items() if len(paths) > 1]
    total = len(candidates)

    for index, (size, paths) in enumerate(candidates, start=1):
        if on_progress:
            on_progress("comparando", index, total)
        for partial_paths in _split_by_hash(paths, PARTIAL_BYTES, result.errors).values():
            if size <= PARTIAL_BYTES:
                full_groups = {_digest_of(partial_paths[0], result.errors): partial_paths}
            else:
                full_groups = _split_by_hash(partial_paths, None, result.errors)
            for digest, same in full_groups.items():
                if digest and len(same) > 1:
                    result.groups.append(DuplicateGroup(size, digest, tuple(sorted(same))))

    result.groups.sort(key=lambda g: (-g.wasted, str(g.paths[0])))
    return result


def _split_by_hash(paths: list[Path], limit: int | None, errors: list[str]) -> dict[str, list[Path]]:
    buckets: dict[str, list[Path]] = defaultdict(list)
    for path in paths:
        try:
            buckets[hash_file(path, limit)].append(path)
        except OSError as exc:
            errors.append(f"{path}: {exc.strerror or exc}")
    return {digest: group for digest, group in buckets.items() if len(group) > 1}


def _digest_of(path: Path, errors: list[str]) -> str:
    try:
        return hash_file(path)
    except OSError as exc:
        errors.append(f"{path}: {exc.strerror or exc}")
        return ""


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path.absolute()
