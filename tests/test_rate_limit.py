from app.rate_limit import LoginLimiter
from tests.test_auth import NEW_USER, register_and_verify

KEY = ("camila@uc.cl", "1.2.3.4")


def login(client, password):
    return client.post("/api/auth/login", json={"email": NEW_USER["email"], "password": password})


def test_blocks_after_five_failures(client, outbox):
    register_and_verify(client, outbox)
    for _ in range(5):
        assert login(client, "Mala-clave-1").status_code == 401

    response = login(client, NEW_USER["password"])
    # Bloqueado aunque ahora la contraseña sea correcta
    assert response.status_code == 429
    assert "Demasiados intentos" in response.json()["detail"]
    assert int(response.headers["retry-after"]) > 0


def test_successful_login_resets_failures(client, outbox):
    register_and_verify(client, outbox)
    for _ in range(4):
        login(client, "Mala-clave-1")
    assert login(client, NEW_USER["password"]).status_code == 200
    for _ in range(4):
        assert login(client, "Mala-clave-1").status_code == 401


def test_unknown_emails_also_count(client):
    for _ in range(5):
        client.post("/api/auth/login", json={"email": "nadie@uc.cl", "password": "x"})
    response = client.post("/api/auth/login", json={"email": "NADIE@uc.cl", "password": "x"})
    assert response.status_code == 429


def test_other_accounts_are_not_blocked(client, outbox):
    register_and_verify(client, outbox)
    for _ in range(5):
        client.post("/api/auth/login", json={"email": "nadie@uc.cl", "password": "x"})
    assert login(client, NEW_USER["password"]).status_code == 200


def test_limiter_unblocks_after_window(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("app.rate_limit.time.monotonic", lambda: now[0])
    limiter = LoginLimiter(max_failures=2, window=60)

    limiter.record_failure(KEY)
    limiter.record_failure(KEY)
    assert limiter.retry_after(KEY) == 60

    now[0] += 30
    assert limiter.retry_after(KEY) == 30
    now[0] += 30
    assert limiter.retry_after(KEY) == 0
