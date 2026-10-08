"""Run all three local processes. No persistent environment configuration required."""

import os
import signal
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
env = {
    **os.environ,
    "DEMO_MODE": "true",
    "DATABASE_URL": os.getenv("DATABASE_URL", f"sqlite:///{root}/sentinel.db"),
    "SANDBOX_DB": os.getenv("SANDBOX_DB", str(root / "sandbox.db")),
    "SANDBOX_URL": "http://127.0.0.1:8001",
}
processes = []
try:
    for command, cwd in [
        (
            [
                sys.executable,
                "-m",
                "uvicorn",
                "sentinel.sandbox:create_sandbox",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                "8001",
            ],
            root,
        ),
        (
            [
                sys.executable,
                "-m",
                "uvicorn",
                "sentinel.api:application",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ],
            root,
        ),
        (["npm", "run", "dev"], root / "frontend"),
    ]:
        processes.append(subprocess.Popen(command, cwd=cwd, env=env))
    print("\nSentinel dashboard: http://127.0.0.1:5173\nAPI docs: http://127.0.0.1:8000/docs\n", flush=True)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    while all(p.poll() is None for p in processes):
        try:
            processes[0].wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
except KeyboardInterrupt:
    pass
finally:
    for p in processes:
        p.terminate()
    for p in processes:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
