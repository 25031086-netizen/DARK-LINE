"""Dark Line - generación procedural de laberintos, escalado de dificultad y utilidades de IA."""
import math
import random
from collections import deque

MAX_LEVEL = 8

# dmg: daño | range: alcance (celdas) | cd: segundos entre ataques | arc: tolerancia angular (rad)
WEAPONS = {
    "espada":  {"dmg": 25, "range": 1.7, "cd": 0.5, "arc": 0.7},
    "lanza":   {"dmg": 20, "range": 2.8, "cd": 0.7, "arc": 0.35},
    "arco":    {"dmg": 18, "range": 9.0, "cd": 1.0, "arc": 0.2},
    "guadana": {"dmg": 50, "range": 2.0, "cd": 1.2, "arc": 0.9},
    "escudo":  {"dmg": 10, "range": 1.3, "cd": 0.4, "arc": 0.8},  # además reduce 60% el daño recibido
}


def params(level):
    k = level - 1
    size = 9 + 3 * k
    if size % 2 == 0:
        size += 1  # el laberinto necesita tamaño impar
    return {
        "size": size,
        "boss_speed": round(1.5 + 0.2 * k, 2),
        "boss_hp": 80 + 50 * k,
        "boss_dmg": 6 + 2 * k,
        "detect": 4 + 0.5 * k,
        "traps": size * size // 60,
    }

def neighbors(g, c):
    x, y = c
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nx, ny = x + dx, y + dy
        if 0 <= ny < len(g) and 0 <= nx < len(g[0]) and g[ny][nx] == 0:
            yield (nx, ny)


def bfs_dist(g, start):
    dist = {start: 0}
    q = deque([start])
    while q:
        c = q.popleft()
        for n in neighbors(g, c):
            if n not in dist:
                dist[n] = dist[c] + 1
                q.append(n)
    return dist


def bfs_path(g, a, b):
    """Camino más corto entre celdas (sin atravesar paredes). Devuelve [a, ..., b] o []."""
    if a == b:
        return [a]
    prev = {a: None}
    q = deque([a])
    while q:
        c = q.popleft()
        if c == b:
            break
        for n in neighbors(g, c):
            if n not in prev:
                prev[n] = c
                q.append(n)
    if b not in prev:
        return []
    path, c = [], b
    while c is not None:
        path.append(c)
        c = prev[c]
    return path[::-1]


def los(g, x0, y0, x1, y1):
    """Línea de visión libre de paredes entre dos puntos (coordenadas en celdas)."""
    d = math.hypot(x1 - x0, y1 - y0)
    steps = int(d / 0.2) + 1
    for i in range(steps + 1):
        t = i / steps
        x, y = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
        if g[int(y)][int(x)] == 1:
            return False
    return True


def generate(level, seed):
    rng = random.Random(seed * 1000 + level)
    n = params(level)["size"]
    g = [[1] * n for _ in range(n)]
    g[1][1] = 0
    stack = [(1, 1)]
    while stack:  # backtracker recursivo
        x, y = stack[-1]
        opts = [(dx, dy) for dx, dy in ((2, 0), (-2, 0), (0, 2), (0, -2))
                if 0 < x + dx < n - 1 and 0 < y + dy < n - 1 and g[y + dy][x + dx] == 1]
        if not opts:
            stack.pop()
            continue
        dx, dy = rng.choice(opts)
        g[y + dy // 2][x + dx // 2] = 0
        g[y + dy][x + dx] = 0
        stack.append((x + dx, y + dy))
    # abrir algunos muros para crear ciclos (el jefe y el jugador pueden rodear)
    for _ in range(n * n // 25):
        x, y = rng.randint(1, n - 2), rng.randint(1, n - 2)
        if g[y][x] == 1 and (x % 2) != (y % 2):
            g[y][x] = 0

    dist = bfs_dist(g, (1, 1))
    cells = sorted(dist)
    far = max(dist.values())
    exit_c = max(cells, key=lambda c: dist[c])
    boss_c = rng.choice([c for c in cells if dist[c] >= far // 2 and c != exit_c])
    used = {(1, 1), exit_c, boss_c}
    pool = [c for c in cells if dist[c] >= 3 and c not in used]
    p = params(level)
    pick = rng.sample(pool, 5 + p["traps"])
    names = list(WEAPONS)
    rng.shuffle(names)
    weapons = [{"k": names[i], "x": pick[i][0], "y": pick[i][1]} for i in range(5)]
    traps = [list(c) for c in pick[5:]]
    return {
        "level": level, "grid": g, "start": [1, 1], "exit": list(exit_c),
        "boss": list(boss_c), "weapons": weapons, "traps": traps,
    }
