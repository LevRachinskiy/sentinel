"""Exercise actual HTTP faults, the worker, human approval, and recovery end to end."""

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def await_server(url):
    for _ in range(100):
        try:
            if httpx.get(url + "/health", trust_env=False).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"Server did not start: {url}")


def await_state(client, incident_id, states):
    for _ in range(150):
        i = client.get(f"/api/incidents/{incident_id}").json()
        if i["status"] in states:
            return i
        time.sleep(0.1)
    raise AssertionError(f"Incident stalled: {i['status']}")


def main():
    api_port, sandbox_port = free_port(), free_port()
    processes = []
    with tempfile.TemporaryDirectory() as directory:
        env = {
            **os.environ,
            "DEMO_MODE": "true",
            "MODEL_PROVIDER": "baseline",
            "DATABASE_URL": f"sqlite:///{directory}/control.db",
            "SANDBOX_DB": f"{directory}/sandbox.db",
            "SANDBOX_URL": f"http://127.0.0.1:{sandbox_port}",
        }
        with open(Path(directory) / "servers.log", "w") as log:
            try:
                for app, port in [
                    ("sentinel.sandbox:create_sandbox", sandbox_port),
                    ("sentinel.api:application", api_port),
                ]:
                    processes.append(
                        subprocess.Popen(
                            [
                                sys.executable,
                                "-m",
                                "uvicorn",
                                app,
                                "--factory",
                                "--host",
                                "127.0.0.1",
                                "--port",
                                str(port),
                            ],
                            env=env,
                            stdout=log,
                            stderr=log,
                        )
                    )
                await_server(f"http://127.0.0.1:{api_port}")
                await_server(f"http://127.0.0.1:{sandbox_port}")
                with httpx.Client(base_url=f"http://127.0.0.1:{api_port}", trust_env=False, timeout=10) as c:
                    for scenario in ["pool_exhaustion", "memory_leak", "dependency_timeout"]:
                        created = c.post("/api/incidents", json={"scenario": scenario, "live": True})
                        created.raise_for_status()
                        incident_id = created.json()["id"]
                        i = await_state(c, incident_id, {"awaiting_approval", "escalated"})
                        assert i["diagnosis"]["cause"] == scenario, i
                        assert i["evidence"]["metrics"]["error_rate"] > 0.1
                        if scenario == "dependency_timeout":
                            assert i["status"] == "escalated"
                            assert not any(e["kind"] == "tool.rollback" for e in i["events"])
                        else:
                            assert i["status"] == "awaiting_approval"
                            time.sleep(0.3)
                            assert (
                                c.get(f"/api/incidents/{incident_id}").json()["status"] == "awaiting_approval"
                            )
                            response = c.post(
                                f"/api/incidents/{incident_id}/decision",
                                json={"decision": "approve", "plan_hash": i["plan_hash"]},
                            )
                            response.raise_for_status()
                            i = await_state(c, incident_id, {"resolved", "escalated"})
                            assert i["status"] == "resolved", i
                            assert i["recovery"]["verified"]
                            assert all(m["error_rate"] == 0 for m in i["recovery"]["samples"])
                        print(f"PASS {scenario}: real HTTP errors -> {i['status']}", flush=True)
                    assert c.get("/api/audit/verify").json()["valid"]
                    print("PASS audit chain integrity", flush=True)
            except Exception:
                log.flush()
                print((Path(directory) / "servers.log").read_text(), file=sys.stderr)
                raise
            finally:
                for p in processes:
                    p.terminate()
                for p in processes:
                    try:
                        p.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        p.kill()


if __name__ == "__main__":
    main()
