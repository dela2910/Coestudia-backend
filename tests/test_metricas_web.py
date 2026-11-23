import json
from datetime import timedelta

from app.models import MetricaWeb, now_utc
from app.routers.metricas_web import percentile_75

URL = "/api/metricas-web"


def metric(nombre="LCP", valor=1800.0, calificacion="good", vista="home", **extra):
    return {"nombre": nombre, "valor": valor, "calificacion": calificacion, "vista": vista, **extra}


def send(client, payload, content_type="text/plain;charset=UTF-8"):
    # Igual que navigator.sendBeacon con un string: texto plano, sin token
    return client.post(URL, content=json.dumps(payload), headers={"Content-Type": content_type})


def test_records_metrics_sent_as_plain_text(client, session_factory):
    payload = [
        metric(navegacion="navigate"),
        metric("CLS", 0.02),
        metric("INP", 350, "needs-improvement"),
    ]
    assert send(client, payload).status_code == 204

    with session_factory() as db:
        saved = db.query(MetricaWeb).order_by(MetricaWeb.id).all()
    assert [(m.nombre, m.valor, m.calificacion) for m in saved] == [
        ("LCP", 1800.0, "good"),
        ("CLS", 0.02, "good"),
        ("INP", 350.0, "needs-improvement"),
    ]
    assert saved[0].navegacion == "navigate"
    assert saved[1].navegacion is None


def test_also_accepts_json_content_type(client):
    assert send(client, [metric()], "application/json").status_code == 204


def test_rejects_invalid_metrics(client):
    assert send(client, [metric("FID")]).status_code == 422
    assert send(client, [metric(valor=-1)]).status_code == 422
    assert send(client, [metric(vista="<script>")]).status_code == 422
    assert send(client, []).status_code == 422
    assert send(client, [metric()] * 21).status_code == 422
    bad_json = client.post(URL, content="no es json")
    assert bad_json.status_code == 422


def test_rejects_large_payloads(client):
    payload = [metric(relleno="x" * 9000)]
    assert send(client, payload).status_code == 413


def test_limits_sends_per_connection(client):
    for _ in range(60):
        assert send(client, [metric()]).status_code == 204
    response = send(client, [metric()])
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0


def test_summary_requires_session(client):
    assert client.get(f"{URL}/resumen").status_code == 401


def test_summary_p75_and_good_share(client, users, session_factory):
    with session_factory() as db:
        for value in (1000, 2000, 3000, 4000):
            rating = "good" if value <= 2500 else "needs-improvement"
            db.add(MetricaWeb(nombre="LCP", valor=value, calificacion=rating, vista="home"))
        db.add(MetricaWeb(nombre="CLS", valor=0.3, calificacion="poor", vista="login"))
        # Fuera de la ventana de 7 días: no cuenta
        db.add(
            MetricaWeb(
                nombre="LCP",
                valor=9000,
                calificacion="poor",
                vista="home",
                creada_en=now_utc() - timedelta(days=8),
            )
        )
        db.commit()

    response = client.get(f"{URL}/resumen", headers=users["ana"])
    assert response.status_code == 200
    assert response.json() == [
        {"nombre": "LCP", "muestras": 4, "p75": 3000.0, "buenas": 0.5},
        {"nombre": "CLS", "muestras": 1, "p75": 0.3, "buenas": 0.0},
    ]

    by_view = client.get(f"{URL}/resumen", params={"vista": "login"}, headers=users["ana"])
    assert [m["nombre"] for m in by_view.json()] == ["CLS"]


def test_percentile_75():
    assert percentile_75([5.0]) == 5.0
    assert percentile_75([4.0, 1.0, 3.0, 2.0]) == 3.0
    assert percentile_75([float(n) for n in range(1, 101)]) == 75.0
