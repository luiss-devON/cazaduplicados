"""Interfaz de línea de comandos de cazaduplicados."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import __version__
from .actions import KEEP_STRATEGIES, quarantine, restore
from .scanner import DEFAULT_EXCLUDE, ScanResult, find_duplicates
from .utils import format_size, parse_size


def _size_arg(text: str) -> int:
    try:
        return parse_size(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cazaduplicados",
        description="Encuentra archivos duplicados por contenido y libera espacio de forma segura.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="comando", required=True)

    def scan_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("rutas", nargs="+", type=Path, help="carpetas o archivos a analizar")
        p.add_argument("--min-tamano", type=_size_arg, default=1, metavar="TAM",
                       help="ignora archivos más pequeños (ej. 100KB). Por defecto: 1 byte")
        p.add_argument("--excluir", action="append", default=[], metavar="NOMBRE",
                       help="nombre de carpeta a ignorar (se puede repetir)")
        p.add_argument("-q", "--silencioso", action="store_true", help="no mostrar progreso")

    p_scan = sub.add_parser("escanear", help="busca duplicados y muestra un informe")
    scan_options(p_scan)
    p_scan.add_argument("--top", type=int, default=20, help="grupos a mostrar (0 = todos)")
    p_scan.add_argument("--json", type=Path, metavar="ARCHIVO", help="guarda el informe completo en JSON")

    p_q = sub.add_parser("cuarentena", help="mueve las copias sobrantes a una carpeta (reversible)")
    scan_options(p_q)
    p_q.add_argument("--destino", type=Path, required=True, help="carpeta de cuarentena")
    p_q.add_argument("--conservar", choices=sorted(KEEP_STRATEGIES), default="antiguo",
                     help="qué copia conservar en cada grupo (por defecto: antiguo)")
    p_q.add_argument("--simular", action="store_true", help="muestra qué haría sin mover nada")

    p_r = sub.add_parser("restaurar", help="devuelve los archivos de la cuarentena a su lugar")
    p_r.add_argument("destino", type=Path, help="carpeta de cuarentena")
    p_r.add_argument("--simular", action="store_true", help="muestra qué haría sin mover nada")
    return parser


def _progress(enabled: bool):
    if not enabled or not sys.stderr.isatty():
        return None

    def report(stage: str, done: int, total: int) -> None:
        end = "\n" if done == total else ""
        print(f"\r{stage}: {done}/{total} grupos de tamaño", end=end, file=sys.stderr, flush=True)

    return report


def _scan(args: argparse.Namespace, extra_exclude_paths=()) -> ScanResult | None:
    missing = [str(p) for p in args.rutas if not p.exists()]
    if missing:
        print(f"Error: no existe: {', '.join(missing)}", file=sys.stderr)
        return None
    return find_duplicates(
        args.rutas,
        min_size=args.min_tamano,
        exclude=DEFAULT_EXCLUDE | set(args.excluir),
        exclude_paths=extra_exclude_paths,
        on_progress=_progress(not args.silencioso),
    )


def _print_report(result: ScanResult, top: int) -> None:
    groups = result.groups if top <= 0 else result.groups[:top]
    for number, group in enumerate(groups, start=1):
        print(f"\nGrupo {number} · {len(group.paths)} copias de {format_size(group.size)}"
              f" · recuperable {format_size(group.wasted)}")
        for path in group.paths:
            print(f"   {path}")
    if len(groups) < len(result.groups):
        print(f"\n... y {len(result.groups) - len(groups)} grupos más (usa --top 0 para verlos todos)")
    print("\n" + "─" * 60)
    print(f"Archivos analizados : {result.files_scanned} ({format_size(result.bytes_scanned)})")
    print(f"Grupos duplicados   : {len(result.groups)}")
    print(f"Copias sobrantes    : {result.duplicate_files}")
    print(f"Espacio recuperable : {format_size(result.wasted_total)}")
    if result.errors:
        print(f"Avisos              : {len(result.errors)} archivos no se pudieron leer")


def cmd_escanear(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    result = _scan(args)
    if result is None:
        return 1
    _print_report(result, args.top)
    print(f"Tiempo              : {time.perf_counter() - started:.2f} s")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Informe JSON        : {args.json}")
    return 0


def cmd_cuarentena(args: argparse.Namespace) -> int:
    result = _scan(args, extra_exclude_paths=[args.destino])
    if result is None:
        return 1
    if not result.groups:
        print("No se encontraron duplicados. ¡Todo limpio!")
        return 0
    moves = quarantine(result.groups, args.destino, strategy=args.conservar, dry_run=args.simular)
    verb = "Se movería" if args.simular else "Movido"
    for move in moves:
        print(f"{verb}: {move.original}\n   (se conserva {move.kept})")
    freed = sum(m.size for m in moves)
    prefix = "[SIMULACIÓN] " if args.simular else ""
    print(f"\n{prefix}{len(moves)} archivos · {format_size(freed)} liberables en el origen.")
    if not args.simular:
        print(f"Para deshacer: cazaduplicados restaurar \"{args.destino}\"")
    return 0


def cmd_restaurar(args: argparse.Namespace) -> int:
    if not args.destino.is_dir():
        print(f"Error: no existe la carpeta {args.destino}", file=sys.stderr)
        return 1
    try:
        restored, problems = restore(args.destino, dry_run=args.simular)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    verb = "Se restauraría" if args.simular else "Restaurado"
    for path in restored:
        print(f"{verb}: {path}")
    for problem in problems:
        print(f"Aviso: {problem}", file=sys.stderr)
    print(f"\n{len(restored)} archivos restaurados, {len(problems)} avisos.")
    return 0 if not problems else 2


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    handlers = {"escanear": cmd_escanear, "cuarentena": cmd_cuarentena, "restaurar": cmd_restaurar}
    try:
        return handlers[args.comando](args)
    except KeyboardInterrupt:
        print("\nCancelado por el usuario.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
