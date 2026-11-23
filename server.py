"""Dark Line - servidor multijugador (FastAPI + WebSockets). Todo el estado vive en memoria."""
import asyncio
import json
import math
import random
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import db
import niveles as N

BASE = Path(__file__).parent
(BASE / "static" / "assets").mkdir(parents=True, exist_ok=True)

SEED = random.randint(1, 10 ** 6)
LEVELS, BOSSES, PLAYERS, RANK = {}, {}, {}, {}
CHARS = {"puno", "pdos", "pres"}


def get_level(l):
    if l not in LEVELS:
        LEVELS[l] = N.generate(l, SEED)
    return LEVELS[l]


def get_boss(l):
    if l not in BOSSES:
        BOSSES[l] = Boss(l)
    return BOSSES[l]


class Boss:
    def __init__(self, lvl):
        self.P = N.params(lvl)
        data = get_level(lvl)
        self.g = data["grid"]
        self.spawn = data["boss"]
        self.cells = list(N.bfs_dist(self.g, (1, 1)).keys())
        self.reset()

    def reset(self):
        self.x, self.y = self.spawn[0] + .5, self.spawn[1] + .5
        self.hp = self.P["boss_hp"]
        self.alive = True
        self.timer = 0.0
        self.path, self.repath, self.cool = [], 0.0, 0.0
        self.chasing = False

    def cell(self):
        return (int(self.x), int(self.y))

    def damage(self, d):
        self.hp -= d
        if self.hp <= 0:
            self.alive, self.timer = False, 28.0  # reaparece a los 20 s
            return True
        return False

    def update(self, dt, players):
        """Devuelve lista de (jugador, daño) causados en este tick."""
        hits = []
        if not self.alive:
            self.timer -= dt
            if self.timer <= 0:
                self.reset()
            return hits
        self.cool -= dt
        self.repath -= dt
        near, nd = None, 1e9
        for p in players:
            if p["dead"]:
                continue
            d = math.hypot(p["x"] - self.x, p["y"] - self.y)
            if d < nd:
                near, nd = p, d
        det = self.P["detect"]
        was = self.chasing
        self.chasing = bool(near and (nd < det or (was and nd < det * 1.6)))
        speed = self.P["boss_speed"]
        if self.chasing:
            if self.repath <= 0:
                self.path = N.bfs_path(self.g, self.cell(), (int(near["x"]), int(near["y"])))
                self.repath = 0.35
            if nd < 1.1:  # cuerpo a cuerpo
                if self.cool <= 0:
                    dmg = self.P["boss_dmg"] * (0.4 if near.get("w") == "escudo" else 1)
                    hits.append((near, dmg))
                    self.cool = 1.0
                return hits
            if len(self.path) <= 1:  # misma celda: ir directo
                dx, dy = near["x"] - self.x, near["y"] - self.y
                d = math.hypot(dx, dy) or 1
                self.x += dx / d * speed * dt
                self.y += dy / d * speed * dt
                return hits
        else:
            speed *= 0.5  # patrulla lenta
            if was or len(self.path) <= 1:
                goal = random.choice(self.cells)
                self.path = N.bfs_path(self.g, self.cell(), goal)
        if len(self.path) > 1:
            tx, ty = self.path[1][0] + .5, self.path[1][1] + .5
            dx, dy = tx - self.x, ty - self.y
            d = math.hypot(dx, dy)
            step = speed * dt
            if d <= step:
                self.x, self.y = tx, ty
                self.path.pop(0)
            else:
                self.x += dx / d * step
                self.y += dy / d * step
        return hits

    def pub(self):
        return {"x": round(self.x, 2), "y": round(self.y, 2), "hp": max(0, int(self.hp)),
                "max": self.P["boss_hp"], "alive": self.alive}


def bump(p, extra=0):
    p["bonus"] += extra
    p["score"] = (p["level"] - 1) * 1000 + p["kills"] * 300 + p["bonus"]


def top10():
    """RANK guarda nombre -> mejor tiempo en segundos (menor es mejor)."""
    return [{"name": n, "tiempo": t} for n, t in sorted(RANK.items(), key=lambda kv: kv[1])[:10]]


async def refresh_rank():
    """Recarga el Top 10 desde la vista top10_tiempo de Neon."""
    try:
        data = await asyncio.to_thread(db.load_top)
        if data:
            RANK.clear()
            RANK.update(data)
    except Exception as e:
        print("No se pudo refrescar el ranking:", e)


async def send(p, msg):
    try:
        await p["ws"].send_text(json.dumps(msg))
    except Exception:
        pass


def level_msg(p, reason):
    d = get_level(p["level"])
    p["x"], p["y"], p["w"] = d["start"][0] + .5, d["start"][1] + .5, None
    return {"t": "level", "reason": reason, "level": p["level"], "data": d}


async def game_loop():
    last, tick = time.monotonic(), 0
    while True:
        await asyncio.sleep(0.05)
        now = time.monotonic()
        dt, last, tick = min(now - last, 0.2), now, tick + 1
        by_level = {}
        for p in list(PLAYERS.values()):
            by_level.setdefault(p["level"], []).append(p)
        for lvl, plist in by_level.items():
            boss = get_boss(lvl)
            for p, dmg in boss.update(dt, plist):
                await send(p, {"t": "hit", "dmg": dmg})
            payload = {
                "t": "tick", "boss": boss.pub(),
                "players": [{"id": p["id"], "name": p["name"], "char": p["char"], "x": round(p["x"], 2),
                             "y": round(p["y"], 2), "rot": round(p["rot"], 2), "dead": p["dead"]}
                            for p in plist],
            }
            if tick % 20 == 0:
                payload["rank"], payload["online"] = top10(), len(PLAYERS)
            for p in plist:
                await send(p, payload)


@asynccontextmanager
async def lifespan(app):
    loop = asyncio.get_running_loop()
    await refresh_rank()
    threading.Thread(
        target=db.listen_thread, daemon=True,
        args=(lambda: asyncio.run_coroutine_threadsafe(refresh_rank(), loop),),
    ).start()
    task = asyncio.create_task(game_loop())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


@app.get("/")
async def index():
    return FileResponse(BASE / "templates" / "index.html")

@app.get("/api/top/{clave}")
async def api_top(clave: str):
    if clave not in db.VISTAS:
        raise HTTPException(404)
    filas = await asyncio.to_thread(db.load_view, clave)
    if not filas and clave == "global":
        filas = [{"puesto": i + 1, "nombre": r["name"], "tiempo_seg": r["tiempo"]}
                 for i, r in enumerate(top10())]
    return filas


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    pid, p = uuid.uuid4().hex[:8], None
    try:
        while True:
            m = json.loads(await ws.receive_text())
            t = m.get("t")
            if t == "join":
                name = (str(m.get("name", "")).strip()[:14]) or "Anónimo"
                ch = m.get("char") if m.get("char") in CHARS else "puno"
                p = {"id": pid, "ws": ws, "name": name, "char": ch, "x": 1.5, "y": 1.5, "rot": 0.0,
                     "level": 1, "w": None, "dead": False, "last": 0.0, "kills": 0, "bonus": 0, "score": 0,
                     "t0": time.monotonic()}
                PLAYERS[pid] = p
                bump(p)
                lm = level_msg(p, "init")
                await send(p, {"t": "init", "id": pid, "weapons": N.WEAPONS, "max_level": N.MAX_LEVEL, "level": lm})
            elif p is None:
                continue
            elif t == "state":
                try:
                    p["x"] = float(m["x"]); p["y"] = float(m["y"]); p["rot"] = float(m["rot"])
                except (KeyError, ValueError, TypeError):
                    continue
                p["w"] = m.get("w") if m.get("w") in N.WEAPONS else None
            elif t == "attack":
                w = N.WEAPONS.get(m.get("w"))
                now = time.monotonic()
                if not w or p["dead"] or now - p["last"] < w["cd"] * 0.8:
                    continue
                p["last"], p["w"] = now, m.get("w")
                b = BOSSES.get(p["level"])
                if b and b.alive:
                    dx, dy = b.x - p["x"], b.y - p["y"]
                    d = math.hypot(dx, dy)
                    fx, fy = -math.sin(p["rot"]), -math.cos(p["rot"])
                    ang_ok = d < 0.8 or (dx * fx + dy * fy) / max(d, 1e-6) >= math.cos(w["arc"])
                    if d <= w["range"] + 0.4 and ang_ok and N.los(b.g, p["x"], p["y"], b.x, b.y):
                        if b.damage(w["dmg"]):
                            p["kills"] += 1
                            bump(p)
                            await send(p, {"t": "msg", "text": "¡Jefe derrotado! Reaparecerá en 20 s"})
            elif t == "advance":
                d = get_level(p["level"])
                if p["dead"] or math.hypot(p["x"] - d["exit"][0] - .5, p["y"] - d["exit"][1] - .5) > 1.5:
                    continue
                if p["level"] >= N.MAX_LEVEL:
                    p["dead"] = True
                    tiempo = round(time.monotonic() - p["t0"], 2)
                    if tiempo < RANK.get(p["name"], 1e12):
                        RANK[p["name"]] = tiempo  # actualización inmediata en memoria
                    asyncio.create_task(asyncio.to_thread(
                        db.save_run, p["name"], p["char"], tiempo, p["kills"]))
                    await send(p, {"t": "win", "tiempo": tiempo})
                else:
                    p["level"] += 1
                    bump(p)
                    await send(p, level_msg(p, "advance"))
            elif t == "dead":
                p["dead"] = True
            elif t == "respawn":
                p["dead"] = False
                await send(p, level_msg(p, "respawn"))
            elif t == "restart":
                p.update(level=1, kills=0, bonus=0, dead=False, t0=time.monotonic())
                await send(p, level_msg(p, "restart"))
    except (WebSocketDisconnect, json.JSONDecodeError):
        pass
    except Exception as e:
        print("ws error:", e)
    finally:
        PLAYERS.pop(pid, None)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000)
