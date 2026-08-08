# PyInstaller spec — one-folder build.
#
# One-folder rather than one-file: a one-file build unpacks to a temp directory on
# every start, which is slower, breaks under a locked-down TEMP policy, and makes
# the running service's files invisible to anyone diagnosing it on the machine.
#
# The migrations directory must ship as data. Without it the service starts, fails
# to build its schema, and returns 500 from every endpoint that touches a table.

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

hidden = [
    # Imported by SQLAlchemy/Alembic through strings, so static analysis misses them.
    *collect_submodules("sqlalchemy.dialects.sqlite"),
    *collect_submodules("alembic"),
    # APScheduler resolves executors and job stores by entry-point name.
    *collect_submodules("apscheduler"),
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "zk",
    "argon2",
]

a = Analysis(
    ["service_main.py"],
    pathex=["."],
    binaries=[],
    datas=[
        ("migrations", "migrations"),
        ("app/static", "app/static"),
        ("app/hrms/profiles", "app/hrms/profiles"),
        # zoneinfo has no system tz database on Windows.
        ("../.venv/Lib/site-packages/tzdata", "tzdata"),
    ],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "mypy", "ruff"],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BiometricBridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="BiometricBridge",
)
