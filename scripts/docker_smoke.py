"""Smoke-check the two built images: the client carries no brain, the agent boots.

CI-2 of `TODO_priorities.md` §Chantier 3quater. `make docker-build` proves the two
images BUILD; this script proves the two claims we had only ever checked by hand:

  1. the `client` image contains no `support_agent`, no `langchain`, no
     `langgraph` — the evidence of step 4's decoupling (deployment plan §4);
  2. the `agent` image really STARTS: its lifespan warms the graph up, uvicorn
     opens its port, `/health` and `/ready` answer, and an unauthenticated call
     is rejected with 401.

Why a stub endpoint (and why that is not cheating). The agent's lifespan builds
the FAQ index before serving, so a container with no reachable embeddings model
cannot boot at all — `/health` would never answer, and the smoke test would be
asserting the absence of a key rather than the health of an image. So we serve
the ONE network dependency of start-up from a local stub speaking the OpenAI
embeddings API, and point the container at it through the project's existing
`openai_compatible` rail. Nothing else is faked: same image, same entrypoint,
same fail-closed start-up, real FAQ, real vector store, real HTTP.

Two things it buys beyond the boot: no secret is needed (this runs on any runner,
which is what keeps it in the guard), and the `openai_compatible` rail — the one
Azure OpenAI uses — gets exercised end to end for the first time.

What it deliberately does NOT do: send a chat message. Answering one needs a real
model (the router alone uses structured output), and quality belongs to
`make score`, not to a container check. This asserts that the door opens.

Standard library only, on purpose: it runs `docker`, and it must not depend on
the venv it is verifying.

⚠️ Read the warm-up duration it prints as a SMOKE number, not as a cold start.
Measured on this rail (2026-07-31, Docker Desktop / arm64): 15 to 25 s across
runs — and the spread is itself the clue. At least **7.3 s is `tiktoken`
downloading `cl100k_base` from an OpenAI CDN** — a call
langchain-openai makes to chunk the text, to a host that has nothing to do with
the endpoint we configured. Verified, not guessed: the same import under
`docker run --network none` fails on `openaipublic.blob.core.windows.net`. The
FAQ itself costs 2 requests for 17 vectors, so the index is not the cost here.
The plan doc's ~4.4 s cold start stands: it was measured on the `mistral` rail,
which does not use tiktoken. Worth knowing for step 5 all the same — an Azure
OpenAI deployment behind a locked-down VNet would not boot until that CDN is
reachable or the encoding is baked into the image.

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

# Named so a leftover container is obvious in `docker ps`, and removable.
CONTAINER = "support-agent-smoke"

# 8102, and not 8000/8001 (local dev) nor 8100/8101 (compose): the project keeps
# host ports disjoint per stack precisely so a probe can never hit the wrong one
# and report on a process nobody is testing (compose.yaml says why, at length).
HOST_PORT = 8102

# The vector width the stub advertises. Small on purpose: the long-term store
# probes the dimension at setup and builds its vector column from the answer, so
# any consistent number works — and a short one keeps the JSON readable in logs.
EMBEDDING_DIM = 8

# Budget for the whole boot: fail-closed check, then the FAQ index (measured at
# ~4.4 s in a container). Generous, because a slow runner must not read as a
# broken image — but bounded, because a hung start-up must not hang the CI.
BOOT_TIMEOUT_S = 120

# The names whose ABSENCE is the point. `langchain_core` matters more than
# `langchain` here: it is what every lang* package drags in, so it is the honest
# tell that no part of the brain rode along in a transitive dependency.
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


# ─────────────────────────────────────────────────────────────────────────────
# The stub embeddings endpoint
# ─────────────────────────────────────────────────────────────────────────────
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
        # The OpenAI API accepts a bare string, a list of strings, or (what
        # langchain-openai actually sends once tiktoken has chunked the text) a
        # list of token lists. Only the COUNT matters to us.
        if isinstance(inputs, str) or (inputs and isinstance(inputs[0], int)):
            count = 1
        else:
            count = len(inputs) or 1

        # ⚠️ The openai SDK asks for `encoding_format: base64` unless told
        # otherwise, and decodes it client-side. A stub that always returned a
        # float list would make the client read floats out of a JSON string —
        # so answer in the format that was requested, like a real endpoint.
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


# ─────────────────────────────────────────────────────────────────────────────
# Check 1 — the client image carries no brain
# ─────────────────────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────────────────────
# Check 2 — the agent image boots and serves
# ─────────────────────────────────────────────────────────────────────────────
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
        # A 401 is an ANSWER here, not a failure: it is what we assert below.
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
            # Lets the container call back to the stub on the host. Needed on
            # Linux (where `host.docker.internal` does not exist by default) and
            # harmless on Docker Desktop, so it is passed unconditionally.
            "--add-host", "host.docker.internal:host-gateway",
            # Realistic configuration, not a permissive one: the key is SET, so
            # the fail-closed start-up is satisfied the way production satisfies
            # it. `API_ALLOW_UNAUTHENTICATED=true` would boot too, and would
            # prove the opposite of what we want to know.
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

        # Fail-closed, verified on the IMAGE and not only in the unit tests: the
        # key is enforced by the artefact we are about to ship, not by a fixture.
        #
        # ⚠️ POST, with a VALID body. Found the hard way while writing this: a GET
        # on `/chat` answers 405 (method not allowed) before any dependency runs,
        # so a probe on the wrong verb reports on routing and says NOTHING about
        # authentication — it would have passed just as happily on a wide-open
        # server. An invalid body has the same defect, one step later (422).
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
        # Both checks always run: two independent claims about two independent
        # images, and knowing only the first of two failures wastes a CI round.
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
