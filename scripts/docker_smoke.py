"""Smoke-check the two built images: the client carries no brain, the agent boots.

Building the images proves they BUILD; this script proves the two claims we had only
ever checked by hand:

    1. the `client` image contains no `support_agent`, no `langchain`, no
       `langgraph` — the evidence of the decoupling;
    2. the `agent` image really STARTS: its lifespan warms the graph up, uvicorn
       opens its port, `/health` and `/ready` answer, and an unauthenticated
       call is rejected with 401.

The agent's lifespan builds the FAQ index before serving, so a container with no
reachable embeddings model cannot boot at all. The ONE network dependency of start-up is
therefore served from a local stub speaking the OpenAI embeddings API, through the
project's existing `openai_compatible` rail. Nothing else is faked: same image, same
entrypoint, same fail-closed start-up, real FAQ, real vector store, real HTTP. No secret
is needed, which is what keeps this in the guard, and that rail — the one Azure OpenAI
uses — gets exercised end to end.

It deliberately does NOT send a chat message: answering one needs a real model, and
quality belongs to `make score`. This asserts that the door opens.

Standard library only, on purpose: it runs `docker`, and must not depend on the venv it
is verifying.

Read the warm-up duration it prints as a SMOKE number, not a cold start. At least 7.3 s
of it is `tiktoken` downloading `cl100k_base` from an OpenAI CDN, a host unrelated to
the endpoint we configured — an Azure OpenAI deployment behind a locked-down VNet would
not boot until that CDN is reachable or the encoding is baked into the image.

    make docker-smoke                        # native architecture
    make docker-smoke PLATFORM=linux/amd64   # what Azure App Service runs
"""

from __future__ import annotations

import base64
import json
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AGENT_IMAGE = "support-agent:dev"
CLIENT_IMAGE = "client-chainlit:dev"

CONTAINER = "support-agent-smoke"

HOST_PORT = 8102

EMBEDDING_DIM = 8

BOOT_TIMEOUT_S = 120

FORBIDDEN_IN_CLIENT = ("support_agent", "langchain", "langchain_core", "langgraph")

_CLIENT_PROBE = """
import importlib.util, sys
names = %r
found = []
for name in names:
    try:
        if importlib.util.find_spec(name) is not None:
            found.append(name)
    except (ImportError, ValueError):
        pass
print("found:" + ",".join(found))
sys.exit(1 if found else 0)
""" % (FORBIDDEN_IN_CLIENT,)


def log(message: str) -> None:
    print(message, flush=True)


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a docker command, capturing both streams."""
    return subprocess.run(args, capture_output=True, text=True, check=check)


class _StubStats:
    """How much the boot asked of the model. Printed, because the warm-up duration
    is meaningless without it: one batched call and thirty round trips look the
    same on the clock only until you know which one happened."""

    calls = 0
    vectors = 0


class _EmbeddingsHandler(BaseHTTPRequestHandler):
    """Answer `POST /v1/embeddings` with deterministic vectors.

    Deterministic, not random: two runs of the same FAQ produce the same index,
    so a failure here can only come from the image.
    """

    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802 (name imposed by BaseHTTPRequestHandler)
        if not self.path.rstrip("/").endswith("/embeddings"):
            self.send_error(404, "only /v1/embeddings is stubbed")
            return

        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self.send_error(400, "invalid JSON")
            return

        inputs = payload.get("input", [])
        if isinstance(inputs, str) or (inputs and isinstance(inputs[0], int)):
            count = 1
        else:
            count = len(inputs) or 1

        _StubStats.calls += 1
        _StubStats.vectors += count

        wants_base64 = payload.get("encoding_format") == "base64"
        vector = [round(0.1 * (i + 1), 3) for i in range(EMBEDDING_DIM)]
        if wants_base64:
            encoded: object = base64.b64encode(
                struct.pack(f"<{EMBEDDING_DIM}f", *vector)
            ).decode("ascii")
        else:
            encoded = vector

        body = json.dumps(
            {
                "object": "list",
                "model": payload.get("model", "stub-embed"),
                "data": [
                    {"object": "embedding", "index": i, "embedding": encoded}
                    for i in range(count)
                ],
                "usage": {"prompt_tokens": count, "total_tokens": count},
            }
        ).encode()

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        """Silence per-request logging: 16 FAQ chunks would bury the real output."""


def start_stub() -> tuple[ThreadingHTTPServer, int]:
    """Serve the stub on an OS-chosen free port, bound so a container can reach it.

    `0.0.0.0`, not localhost: the caller is in another network namespace.
    """
    server = ThreadingHTTPServer(("0.0.0.0", 0), _EmbeddingsHandler)  # noqa: S104
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def check_client_has_no_brain() -> bool:
    log(f"→ {CLIENT_IMAGE}: aucun module du cerveau ?")
    result = run(
        "docker", "run", "--rm", "--entrypoint", "python",
        CLIENT_IMAGE, "-c", _CLIENT_PROBE,
        check=False,
    )
    if result.returncode == 0:
        log(f"  ✅ absents : {', '.join(FORBIDDEN_IN_CLIENT)}")
        return True

    log(f"  ❌ le découplage de l'étape 4 a régressé — {result.stdout.strip()}")
    if result.stderr.strip():
        log(f"     stderr: {result.stderr.strip()}")
    return False


def _call(path: str, *, json_body: dict[str, object] | None = None) -> tuple[int, str]:
    """Call the containerised agent, treating an HTTP error status as an answer."""
    data = None
    headers = {}
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"http://127.0.0.1:{HOST_PORT}{path}", data=data, headers=headers
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def _container_died() -> str | None:
    """Return the container's logs if it is no longer running, else None.

    Without this, a fail-closed refusal to start (the whole point of the
    lifespan) would show up as a polling timeout — the least informative
    possible rendering of a message that says exactly what is wrong.
    """
    state = run("docker", "inspect", "-f", "{{.State.Running}}", CONTAINER, check=False)
    if state.stdout.strip() == "true":
        return None
    return run("docker", "logs", CONTAINER, check=False).stdout + run(
        "docker", "logs", CONTAINER, check=False
    ).stderr


def check_agent_boots(stub_port: int) -> bool:
    log(f"→ {AGENT_IMAGE}: démarre, s'échauffe, et répond ?")
    run("docker", "rm", "-f", CONTAINER, check=False)

    started = subprocess.run(
        [
            "docker", "run", "-d", "--name", CONTAINER,
            "-p", f"{HOST_PORT}:8000",
            "--add-host", "host.docker.internal:host-gateway",
            "-e", "API_KEY=smoke-key",
            "-e", "LLM_PROVIDER=openai_compatible",
            "-e", "LLM_MODEL=stub-chat",
            "-e", "EMBEDDINGS_PROVIDER=openai_compatible",
            "-e", "EMBEDDINGS_MODEL=stub-embed",
            "-e", f"LLM_INFERENCE_ENDPOINT=http://host.docker.internal:{stub_port}/v1",
            "-e", "LLM_INFERENCE_API_KEY=stub",
            AGENT_IMAGE,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if started.returncode != 0:
        log(f"  ❌ `docker run` a échoué : {started.stderr.strip()}")
        return False

    try:
        deadline = time.monotonic() + BOOT_TIMEOUT_S
        ready_at: float | None = None
        boot_started = time.monotonic()
        while time.monotonic() < deadline:
            logs = _container_died()
            if logs is not None:
                log("  ❌ le conteneur s'est arrêté avant de servir. Ses logs :")
                log("     " + "\n     ".join(logs.strip().splitlines()[-25:]))
                return False
            try:
                status, _ = _call("/health")
            except OSError:
                time.sleep(1)
                continue
            if status != 200:
                log(f"  ❌ /health répond {status}")
                return False
            status, body = _call("/ready")
            if status == 200 and json.loads(body).get("ready") is True:
                ready_at = time.monotonic() - boot_started
                break
            time.sleep(1)

        if ready_at is None:
            log(f"  ❌ toujours pas prêt après {BOOT_TIMEOUT_S} s")
            log("     " + "\n     ".join(
                run("docker", "logs", CONTAINER, check=False).stderr.strip().splitlines()[-25:]
            ))
            return False
        log(
            f"  ✅ /health et /ready répondent (échauffement {ready_at:.1f} s, "
            f"{_StubStats.calls} appels embeddings / {_StubStats.vectors} vecteurs)"
        )

        status, _ = _call("/chat", json_body={"message": "smoke"})
        if status != 401:
            log(f"  ❌ POST /chat sans clé renvoie {status}, on attend 401")
            return False
        log("  ✅ POST /chat sans X-API-Key : 401")
        return True
    finally:
        run("docker", "rm", "-f", CONTAINER, check=False)


def main() -> int:
    stub, stub_port = start_stub()
    log(f"Stub embeddings sur le port {stub_port} (dim {EMBEDDING_DIM}, aucun secret).")
    try:
        results = [check_client_has_no_brain(), check_agent_boots(stub_port)]
    finally:
        stub.shutdown()

    if all(results):
        log("→ Les deux images tiennent leurs promesses.")
        return 0
    log("→ ÉCHEC : voir ci-dessus.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
