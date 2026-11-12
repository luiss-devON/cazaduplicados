"""cazaduplicados: encuentra archivos duplicados y libera espacio de forma segura."""

from .scanner import DuplicateGroup, ScanResult, find_duplicates
from .actions import quarantine, restore

__all__ = ["DuplicateGroup", "ScanResult", "find_duplicates", "quarantine", "restore"]
__version__ = "1.0.0"
