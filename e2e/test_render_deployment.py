"""Exercise the Blueprint's Gunicorn entry point through a local HTTPS proxy."""

from http.client import HTTPConnection
import os
import re
import shlex
import socket
import ssl
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from flask import Flask, Response, request
import pytest

from e2e.conftest import ROOT, _server, _serving, _tls_files


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Gunicorn is exercised on Linux CI")
def test_render_blueprint_serves_same_origin_over_https(tmp_path):
    with socket.socket() as port_reservation:
        port_reservation.bind(("127.0.0.1", 0))
        port = port_reservation.getsockname()[1]

    proxy = Flask("render-edge-test")

    @proxy.get("/", defaults={"path": ""})
    @proxy.get("/<path:path>")
    def forward(path):
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request("GET", f"/{path}", headers={"Host": request.host})
            upstream = connection.getresponse()
            headers = [(key, value) for key, value in upstream.getheaders()
                       if key.lower() in {"content-type", "referrer-policy", "cache-control",
                                          "strict-transport-security"}]
            return Response(upstream.read(), status=upstream.status, headers=headers)
        except OSError:
            return Response(status=502)
        finally:
            connection.close()

    tls = _tls_files(tmp_path)
    tls_context = ssl.create_default_context(cafile=tls[0])
    blueprint = (ROOT / "render.yaml").read_text(encoding="utf-8")
    command = re.search(r"^    startCommand: >-\n((?:      .+\n)+)", blueprint, re.MULTILINE)
    assert command, "Expected the Blueprint's folded Gunicorn start command"
    argv = shlex.split(command.group(1).replace("$PORT", str(port)))
    assert argv[0] == "python"
    argv[0] = sys.executable

    with _serving(_server(proxy, tls)) as origin:
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("MAIL_", "PG", "RENDER"))}
        env.update(
            APP_ENV="production", PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1",
            SECRET_KEY="synthetic-secret-for-local-gunicorn-smoke-test",
            FRONTEND_URL=origin, SESSION_COOKIE_SECURE="true", SESSION_COOKIE_SAMESITE="Lax",
            RATE_LIMIT_ENABLED="true", GEMINI_API_KEY="", GOOGLE_API_KEY="", API_KEY="",
            DATABASE_URL=("postgresql://smoke:dummy@127.0.0.1:1/postgres"
                          f"?sslmode=verify-full&sslrootcert={tls[0]}"),
        )
        log = tmp_path / "gunicorn.log"
        with log.open("wb") as output:
            process = subprocess.Popen(argv, cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and process.poll() is None:
                    try:
                        with urlopen(origin + "/health", context=tls_context, timeout=3) as response:
                            assert response.status == 200
                            break
                    except URLError:
                        time.sleep(0.1)
                else:
                    pytest.fail(log.read_text(encoding="utf-8", errors="replace"))

                with urlopen(origin + "/auth.js", context=tls_context, timeout=5) as script:
                    assert b'"/api/auth"' in script.read()
                with urlopen(origin + "/reset-password", context=tls_context, timeout=5) as reset:
                    assert reset.headers["Cache-Control"] == "no-store"
                    assert reset.headers["Referrer-Policy"] == "no-referrer"
                    assert "max-age=" in reset.headers["Strict-Transport-Security"]
                with pytest.raises(HTTPError) as unavailable:
                    urlopen(origin + "/ready", context=tls_context, timeout=5)
                with unavailable.value as response:
                    assert response.code == 503
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
