"""Dark Line - ranking en Neon (Postgres). Sin DATABASE_URL todo sigue funcionando en memoria."""
import os
import time

import psycopg

URL = os.getenv("DATABASE_URL")
# LISTEN/NOTIFY NO funciona con la conexión "pooler" de Neon: usa la directa (sin "-pooler" en el host).
URL_DIRECT = os.getenv("DATABASE_URL_DIRECT") or URL


def load_top():
    """Lee la vista top10_tiempo -> {nombre: tiempo_seg}."""
    if not URL:
        return {}
    with psycopg.connect(URL) as c:
        return {n: float(t) for n, t in c.execute("SELECT nombre, tiempo_seg FROM top10_tiempo")}


def save_run(name, char, tiempo, jefes):
    """Inserta una partida terminada; los triggers de Neon hacen el resto."""
    if not URL:
        return
    try:
        with psycopg.connect(URL) as c:
            c.execute(
                "INSERT INTO partidas (nombre, personaje, tiempo_seg, jefes_derrotados) VALUES (%s, %s, %s, %s)",
                (name, char, tiempo, jefes))
    except Exception as e:
        print("db error:", e)


def listen_thread(on_change):
    """Hilo que escucha NOTIFY 'ranking_cambio' y llama a on_change() en tiempo real."""
    if not URL_DIRECT:
        return
    while True:
        try:
            with psycopg.connect(URL_DIRECT, autocommit=True) as conn:
                conn.execute("LISTEN ranking_cambio")
                for _ in conn.notifies():
                    on_change()
        except Exception as e:
            print("listen error:", e)
            time.sleep(5)


from decimal import Decimal

VISTAS = {"global": "top10_tiempo", "hoy": "top10_hoy"}


def load_view(clave):
    if not URL or clave not in VISTAS:
        return []
    try:
        with psycopg.connect(URL) as c:
            cur = c.execute(f"SELECT * FROM {VISTAS[clave]}")
            cols = [d.name for d in cur.description]
            return [{k: (float(v) if isinstance(v, Decimal) else v) for k, v in zip(cols, row)}
                    for row in cur.fetchall()]
    except Exception as e:
        print("db error:", e)
        return []