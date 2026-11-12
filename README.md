# 🔍 cazaduplicados

[![tests](https://github.com/luiss-devON/cazaduplicados/actions/workflows/tests.yml/badge.svg)](https://github.com/luiss-devON/cazaduplicados/actions/workflows/tests.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Dependencias](https://img.shields.io/badge/dependencias-0-brightgreen)
![Licencia](https://img.shields.io/badge/licencia-MIT-green)

Herramienta de línea de comandos que **encuentra archivos duplicados por su contenido** (no por el nombre) y te ayuda a **recuperar espacio en disco de forma segura**: nada se borra, las copias sobrantes van a una cuarentena que se puede deshacer con un solo comando.

Útil para limpiar la carpeta de Descargas, fotos repetidas del celular, respaldos duplicados, discos externos, etc.

## ✨ Características

- **Rápido**: algoritmo en 3 fases que evita leer archivos innecesariamente
  1. agrupa por tamaño (solo metadatos),
  2. compara un hash de los primeros 64 KB,
  3. calcula el hash completo (BLAKE2b) solo de los candidatos que siguen empatados.
- **Seguro**: modo `--simular`, cuarentena con manifiesto JSON y restauración que **nunca sobrescribe**.
- **Correcto**: ignora enlaces simbólicos, no confunde enlaces duros ni rutas solapadas con duplicados, y verifica que el archivo no haya cambiado antes de moverlo.
- **Informes**: resumen en consola ordenado por espacio recuperable y exportación a JSON.
- **Sin dependencias**: solo la biblioteca estándar de Python. Funciona en Windows y Linux.

> ⚠️ **macOS: no funciona por ahora.** Solo está probado y soportado en Windows y Linux.

## 📦 Instalación

```bash
git clone https://github.com/luiss-devON/cazaduplicados.git
cd cazaduplicados
pip install .
```

## 🚀 Uso

```bash
# 1. Ver qué duplicados hay (no modifica nada)
cazaduplicados escanear ~/Descargas ~/Imágenes

# Solo archivos de más de 1 MB, ignorando node_modules, y guardar informe JSON
cazaduplicados escanear ~/Proyectos --min-tamano 1MB --excluir node_modules --json informe.json

# 2. Probar qué se movería a cuarentena (simulación)
cazaduplicados cuarentena ~/Descargas --destino ~/cuarentena --simular

# 3. Mover las copias sobrantes, conservando la más antigua de cada grupo
cazaduplicados cuarentena ~/Descargas --destino ~/cuarentena --conservar antiguo

# 4. ¿Te arrepentiste? Todo vuelve a su lugar
cazaduplicados restaurar ~/cuarentena
```

Ejemplo de salida:

```text
Grupo 1 · 3 copias de 245.3 MB · recuperable 490.6 MB
   /home/ana/Descargas/video.mp4
   /home/ana/Descargas/video (1).mp4
   /home/ana/Escritorio/video.mp4

────────────────────────────────────────────────────────────
Archivos analizados : 4812 (18.2 GB)
Grupos duplicados   : 37
Copias sobrantes    : 52
Espacio recuperable : 1.9 GB
Tiempo              : 3.41 s
```

Estrategias para elegir qué copia conservar (`--conservar`): `antiguo` (por defecto), `reciente`, `ruta-corta`.

También se puede usar como librería:

```python
from cazaduplicados import find_duplicates

resultado = find_duplicates(["/ruta/a/carpeta"], min_size=1024)
for grupo in resultado.groups:
    print(grupo.size, grupo.paths)
```

## 🧪 Tests

```bash
python -m unittest -v
```

## 🗂️ Estructura

```text
src/cazaduplicados/
├── scanner.py   # recorrido del disco y detección de duplicados
├── actions.py   # cuarentena y restauración con manifiesto
├── cli.py       # interfaz de línea de comandos (argparse)
└── utils.py     # formato y lectura de tamaños
tests/           # 20 tests con unittest
```

## 📄 Licencia

MIT
