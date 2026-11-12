"""Acciones seguras sobre duplicados: cuarentena reversible y restauración.

Nunca se borra nada: las copias sobrantes se mueven a una carpeta de cuarentena
y se registra cada movimiento en `manifiesto.json` para poder deshacerlo.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from .scanner import QUARANTINE_MARKER, DuplicateGroup

MANIFEST_NAME = "manifiesto.json"


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


KEEP_STRATEGIES: dict[str, Callable[[Path], tuple]] = {
    "antiguo": lambda p: (_mtime(p), str(p)),
    "reciente": lambda p: (-_mtime(p), str(p)),
    "ruta-corta": lambda p: (len(str(p)), str(p)),
}


@dataclass(frozen=True)
class Move:
    original: Path
    destination: Path
    kept: Path
    size: int


def choose_keeper(paths: Iterable[Path], strategy: str = "antiguo") -> Path:
    """Elige qué copia conservar según la estrategia indicada."""
    if strategy not in KEEP_STRATEGIES:
        raise ValueError(f"estrategia desconocida: {strategy!r}; usa una de {sorted(KEEP_STRATEGIES)}")
    return min(paths, key=KEEP_STRATEGIES[strategy])


def quarantine(
    groups: Iterable[DuplicateGroup],
    destination: str | os.PathLike,
    *,
    strategy: str = "antiguo",
    dry_run: bool = False,
) -> list[Move]:
    """Mueve las copias sobrantes de cada grupo a `destination`.

    Antes de mover cada archivo verifica que siga existiendo con el mismo tamaño,
    por si cambió desde el escaneo.
    """
    dest = Path(destination)
    manifest = _load_manifest(dest)
    moves: list[Move] = []
    counter = len(manifest)

    if not dry_run:
        dest.mkdir(parents=True, exist_ok=True)
        (dest / QUARANTINE_MARKER).write_text(
            "Carpeta de cuarentena de cazaduplicados. No se analiza en los escaneos.\n", encoding="utf-8"
        )

    try:
        for group in groups:
            keeper = choose_keeper(group.paths, strategy)
            for path in group.paths:
                if path == keeper:
                    continue
                try:
                    if path.stat().st_size != group.size:
                        continue
                except OSError:
                    continue
                counter += 1
                target = dest / f"{counter:05d}_{path.name}"
                move = Move(path, target, keeper, group.size)
                if not dry_run:
                    shutil.move(str(path), str(target))
                    manifest.append(
                        {
                            "original": str(path.absolute()),
                            "cuarentena": target.name,
                            "conservado": str(keeper.absolute()),
                            "tamano": group.size,
                            "fecha": datetime.now().isoformat(timespec="seconds"),
                        }
                    )
                moves.append(move)
    finally:
        if not dry_run and moves:
            _save_manifest(dest, manifest)
    return moves


def restore(destination: str | os.PathLike, *, dry_run: bool = False) -> tuple[list[Path], list[str]]:
    """Devuelve a su lugar original los archivos en cuarentena.

    Retorna (rutas_restauradas, problemas). Si en la ruta original ya existe un
    archivo, no se sobrescribe y se informa como problema.
    """
    dest = Path(destination)
    manifest = _load_manifest(dest)
    restored: list[Path] = []
    problems: list[str] = []
    pending: list[dict] = []

    try:
        for entry in manifest:
            source = dest / entry["cuarentena"]
            original = Path(entry["original"])
            if not source.exists():
                problems.append(f"falta en cuarentena: {source}")
                continue
            if original.exists():
                problems.append(f"ya existe, no se sobrescribe: {original}")
                pending.append(entry)
                continue
            if not dry_run:
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(original))
            else:
                pending.append(entry)
            restored.append(original)
    finally:
        if not dry_run and manifest:
            if pending:
                _save_manifest(dest, pending)
            else:
                (dest / MANIFEST_NAME).unlink(missing_ok=True)
    return restored, problems


def _load_manifest(dest: Path) -> list[dict]:
    path = dest / MANIFEST_NAME
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError(f"manifiesto inválido: {path}")
    return data


def _save_manifest(dest: Path, entries: list[dict]) -> None:
    path = dest / MANIFEST_NAME
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(entries, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
