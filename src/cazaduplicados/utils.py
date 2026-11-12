"""Utilidades para mostrar y leer tamaños de archivo."""

from __future__ import annotations

import re

_UNITS = ["B", "KB", "MB", "GB", "TB"]
_FACTORS = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
_SIZE_RE = re.compile(r"\s*(\d+(?:[.,]\d+)?)\s*([KMGT]?B?)\s*", re.IGNORECASE)


def format_size(num_bytes: int) -> str:
    """Convierte bytes en un texto legible: 1536 -> '1.5 KB'."""
    size = float(num_bytes)
    for unit in _UNITS:
        if size < 1024 or unit == _UNITS[-1]:
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("inalcanzable")


def parse_size(text: str) -> int:
    """Convierte '10MB', '1.5 GB', '512k' o '200' en bytes."""
    match = _SIZE_RE.fullmatch(text)
    if not match:
        raise ValueError(f"tamaño no válido: {text!r} (ejemplos: 500, 10KB, 1.5MB)")
    number = float(match.group(1).replace(",", "."))
    unit = match.group(2).upper().rstrip("B")
    return int(number * _FACTORS[unit])
