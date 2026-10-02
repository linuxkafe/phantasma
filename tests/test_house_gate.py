"""Ninguém muda a casa sem credencial. Medido, não lido.

O achado (2026-10-02): `POST /api/devices/<name>/control` chegava ao handler
sem sessão e sem token. Não por estar numa lista errada — por **não estar em
nenhuma**: `_TOKEN_PATHS` é um conjunto de comparação exacta e um path com um
segmento variável não se pode escrever nele. As outras três rotas de acção
estavam gated e eu escrevi no ROADMAP que não estavam. Verificar antes de
reportar teria apanhado isso.

Reproduzido na app isolada, sem qualquer credencial:

    /comando                  -> 401
    /device_action            -> 401
    /api/command              -> 401
    /api/devices/luz/control  -> 404 {"error":"Device not found"}

O 404 é o handler a **correr**. "Device not found" é a resposta dele para um nome
que não conhece; com um nome real teria mudado o dispositivo.

Porque isto importa mais agora: o T068 vai dar sessões a convidados que podem ler
o RAG e as memórias. A gate de comandos passa a ser a única coisa entre um
convidado e a casa. Se ela tem um buraco, o papel não é o que protege.

Este ficheiro é parametrizado por TODA a rota que toca a casa, precisamente
para o próximo buraco não aparecer numa rota que ninguém listou. Acrescentar uma
rota nova de acção sem a meter na lista tem de partir um teste.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.api import admin as admin_mod  # noqa: E402

# (path, method, body). Every one of these either writes to a device or exposes
# the house. GETs are included because reading the inventory of a private home
# was a live finding once already (routes.py, 2026-09-29).
HOUSE_ROUTES = [
    ("/comando", "POST", {"prompt": "acende a luz da sala"}),
    ("/device_action", "POST", {"action": "turn_on", "name": "luz"}),
    ("/api/command", "POST", {"prompt": "acende a luz da sala"}),
    ("/get_devices", "GET", None),
    ("/api/devices", "GET", None),
    ("/api/devices/luz/control", "POST", {"device_name": "luz", "action": "turn_on"}),
    ("/device_status", "GET", None),
]


@pytest.fixture
def bare_client(tmp_path, monkeypatch):
    """A Flask test client with NO session and NO bearer token.

    Deliberately not the conftest `admin_client` fixture: that one signs in as an
    admin, which is the opposite of what these tests are for.

    FUNCTION-scoped and via `monkeypatch`, and the reason is a lesson. The first
    version of this fixture set `config.DB_PATH`, `config.BRAIN_DB_PATH` and the
    two `admin` module constants at module scope and never restored them. Both
    files passed on their own and the full suite lost six tests in
    test_quiet.py and test_localauth.py -- global state leaking forward, the same
    shape as the T057 finding about a suite reaching the production database.
    `monkeypatch` restores every attribute when the test ends; `tmp_path` cleans
    the databases.
    """
    schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    for name in ("config.db", "brain.db"):
        con = sqlite3.connect(tmp_path / name)
        con.executescript(schema)
        con.commit()
        con.close()

    import config as cfg

    for holder, attrs in (
        (cfg, ("CONFIG_DB_PATH", "BRAIN_DB_PATH", "DB_PATH")),
        (cfg.config, ("config_db_path", "brain_db_path", "db_path")),
        (admin_mod, ("CONFIG_DB_PATH", "BRAIN_DB_PATH")),
    ):
        for attr in attrs:
            if hasattr(holder, attr):
                monkeypatch.setattr(
                    holder, attr, str(tmp_path / ("config.db" if "config" in attr else "brain.db"))
                )

    from src.api.routes import create_app

    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@pytest.mark.parametrize("path,method,body", HOUSE_ROUTES, ids=[r[0] for r in HOUSE_ROUTES])
def test_anonymous_cannot_reach_the_house(bare_client, path, method, body):
    """The invariant. 401, and the handler never runs.

    A 404 here would be a FAILURE dressed as a pass: it means the route reached
    its body. That is exactly the shape the hole had.
    """
    resp = bare_client.open(path, method=method, json=body)
    assert resp.status_code == 401, (
        f"{path} respondeu {resp.status_code} sem credencial: "
        f"{resp.get_data(as_text=True)[:160]}"
    )


def test_the_device_control_route_is_the_one_that_was_open(bare_client):
    """Named specifically, because this is the route that was broken.

    The other three command routes were gated all along and I reported that they
    were not. The gate compares exact paths, and a path with a `<name>` segment
    cannot be written in that set -- which is how the one that mattered was the
    one that slipped through.
    """
    resp = bare_client.post(
        "/api/devices/luz/control",
        json={"device_name": "luz", "action": "turn_on"},
    )
    assert resp.status_code == 401, (
        "POST /api/devices/<name>/control voltou a responder sem credencial"
    )
    assert "Device not found" not in resp.get_data(as_text=True), (
        "o handler voltou a correr: a resposta e a dele, nao a da gate"
    )


def test_a_valid_session_still_gets_through(bare_client):
    """Closing the gate must not become a denial of service on the owner.

    `/comando` is what the Discord skill and the UI speak to; if this fails,
    the voice path is broken and that is worse than the hole.
    """
    with bare_client.session_transaction() as sess:
        sess["ui_user"] = "owner@example.invalid"

    from src.api import ui_auth

    orig = ui_auth.is_authenticated
    ui_auth.is_authenticated = lambda: True
    try:
        resp = bare_client.post("/comando", json={"prompt": "que horas sao"})
        assert resp.status_code != 401, "uma sessao valida esta a ser recusada"
    finally:
        ui_auth.is_authenticated = orig
