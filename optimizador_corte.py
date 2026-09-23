"""
optimizador_corte.py — Cutting stock ÓPTIMO para placas PVC (ILP por patrones).

En vez de decidir pieza por pieza en qué largo de placa va (heurística),
enumera todas las formas "llenas" de cortar cada largo de placa y un solver
elige la combinación que cubre todas las piezas al menor costo total.

Devuelve el plan con el mismo formato que resolver_mixto():
    [{"largo_placa": 6, "cortes": [{"dim": 4.8, "hab_idx": 2}, ...], "libre": 1.2}, ...]
"""
from collections import Counter, defaultdict

try:
    import pulp
    HAY_PULP = True
except ImportError:          # si no está instalado, la app sigue con las heurísticas
    HAY_PULP = False

_MM = 1000  # trabajamos en milímetros enteros: evita errores de redondeo tipo 5.9999


def _mm(x):
    return int(round(x * _MM))


def _patrones(L_mm, dims, demanda, kerf):
    """
    Enumera patrones MAXIMALES de corte para una placa de L_mm:
    combinaciones de piezas donde ya no entra ninguna pieza más.
    (Un patrón no maximal nunca conviene: cuesta lo mismo y produce menos.)
    Con kerf, n piezas ocupan n*d + (n-1)*kerf  ->  capacidad efectiva L+kerf.
    """
    dims = sorted(dims, reverse=True)
    cap = L_mm + kerf
    pats = []

    def rec(i, libre, cuenta):
        if i == len(dims):
            maximal = all(cuenta[j] >= demanda[d] or d + kerf > libre
                          for j, d in enumerate(dims))
            if maximal and any(cuenta):
                pats.append({d: c for d, c in zip(dims, cuenta) if c})
            return
        d = dims[i]
        tope = min(demanda[d], libre // (d + kerf))
        for c in range(tope, -1, -1):
            cuenta[i] = c
            rec(i + 1, libre - c * (d + kerf), cuenta)
        cuenta[i] = 0

    rec(0, cap, [0] * len(dims))
    return pats


def costo_plan(plan, precios=None):
    """Costo de un plan: en $ si hay precio cargado para todos los largos usados,
    si no, en metros lineales comprados."""
    if not plan:
        return float("inf")
    usados = {b["largo_placa"] for b in plan}
    if precios and all(precios.get(L, 0) > 0 for L in usados):
        return sum(precios[b["largo_placa"]] for b in plan)
    return sum(b["largo_placa"] for b in plan)


def resolver_optimo(piezas, lens, precios=None, kerf=0.0, limite_seg=15):
    """
    piezas : [{"dim": float, "hab_idx": int}, ...]
    lens   : largos de placa disponibles, ej. [4, 6]
    precios: {4: $, 5: $, 6: $}. Si están cargados (>0) para todos los largos
             disponibles, minimiza PESOS; si no, minimiza METROS comprados.
    kerf   : espesor de corte de la sierra en metros (0.003 = 3 mm). Por defecto 0.
    Devuelve el plan, o None si no hay solver / no encontró solución.
    """
    if not HAY_PULP or not piezas or not lens:
        return None

    kerf_mm = _mm(kerf)
    demanda = Counter(_mm(p["dim"]) for p in piezas)
    dims = list(demanda)
    if max(dims) > _mm(max(lens)):
        return None  # hay piezas que no entran en ninguna placa (falta perfil H)

    usar_precio = bool(precios) and all(precios.get(L, 0) > 0 for L in lens)

    # 1) Patrones por largo de placa
    columnas = []  # (L, patrón)
    for L in lens:
        for pat in _patrones(_mm(L), dims, demanda, kerf_mm):
            columnas.append((L, pat))

    # 2) Modelo: x_k = cuántas placas se cortan con el patrón k
    prob = pulp.LpProblem("corte_pvc", pulp.LpMinimize)
    x = [pulp.LpVariable(f"x{k}", lowBound=0, cat="Integer") for k in range(len(columnas))]

    def costo(L):
        base = precios[L] if usar_precio else L
        return base * 1000 + 1   # +1: a igual costo, preferir menos placas

    prob += pulp.lpSum(costo(L) * x[k] for k, (L, _) in enumerate(columnas))
    for d in dims:
        prob += pulp.lpSum(pat.get(d, 0) * x[k]
                           for k, (_, pat) in enumerate(columnas)) >= demanda[d]

    prob.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=limite_seg))
    estado = pulp.LpStatus[prob.status]
    if estado in ("Infeasible", "Unbounded") or any(v.value() is None for v in x):
        return None

    # 3) Expandir a placas físicas
    placas = []
    for k, (L, pat) in enumerate(columnas):
        n = int(round(x[k].value() or 0))
        for _ in range(n):
            placas.append({"L": L, "cnt": dict(pat)})

    # 4) Quitar piezas sobrantes (el modelo usa >=, puede producir de más)
    producido = Counter()
    for p in placas:
        producido.update(p["cnt"])
    for d in dims:
        exceso = producido[d] - demanda[d]
        # sacar primero de las placas que más copias tienen
        for p in sorted(placas, key=lambda p: -p["cnt"].get(d, 0)):
            if exceso <= 0:
                break
            quita = min(exceso, p["cnt"].get(d, 0))
            p["cnt"][d] = p["cnt"].get(d, 0) - quita
            exceso -= quita
    placas = [p for p in placas if sum(p["cnt"].values()) > 0]

    # 5) Asignar habitación a cada corte y armar formato de la app
    cola = defaultdict(list)
    for p in sorted(piezas, key=lambda p: p["hab_idx"]):
        cola[_mm(p["dim"])].append(p)

    plan = []
    for p in placas:
        cortes = []
        for d in sorted(p["cnt"], reverse=True):
            for _ in range(p["cnt"][d]):
                orig = cola[d].pop(0)
                cortes.append({"dim": orig["dim"], "hab_idx": orig["hab_idx"]})
        n = len(cortes)
        usado_mm = sum(_mm(c["dim"]) for c in cortes) + kerf_mm * max(0, n - 1)
        plan.append({"largo_placa": p["L"], "cortes": cortes,
                     "libre": max(0.0, (_mm(p["L"]) - usado_mm) / _MM)})

    # orden prolijo: por largo, y placas iguales juntas
    plan.sort(key=lambda b: (b["largo_placa"],
                             [-c["dim"] for c in b["cortes"]]))
    return plan


def mejorar_orientaciones(hab_info, lens, pw, precios=None, kerf=0.0):
    """
    Búsqueda local de orientación con el optimizador GLOBAL.
    Para cada habitación rectangular sin sentido fijo y sin perfil H, prueba
    girar las placas y se queda con el giro si baja el costo total de la obra.
    Modifica hab_info en el lugar y devuelve el plan final.
    """
    import math

    def todas():
        return [p for h in hab_info for p in h["piezas"]]

    plan = resolver_optimo(todas(), lens, precios, kerf)
    if plan is None:
        return None
    mejor = costo_plan(plan, precios)

    for _ in range(2):                      # 2 pasadas alcanzan en la práctica
        hubo_mejora = False
        for h in hab_info:
            if h.get("tipo") == "l" or h.get("fijo") or h.get("n_h", 0) > 0:
                continue
            if h["orient"] == "largo":
                n_or, n_dim, n_fil = "ancho", h["ancho"], math.ceil(round(h["largo"] / pw, 6))
            else:
                n_or, n_dim, n_fil = "largo", h["largo"], math.ceil(round(h["ancho"] / pw, 6))
            if n_dim > max(lens) + 1e-4:
                continue                    # girar obligaría a usar H
            viejo = {k: h[k] for k in ("orient", "dim_pieza", "filas", "piezas", "segmentos")}
            h.update(orient=n_or, dim_pieza=n_dim, filas=n_fil, segmentos=[n_dim],
                     piezas=[{"dim": n_dim, "hab_idx": h["idx"]}] * n_fil)
            cand = resolver_optimo(todas(), lens, precios, kerf)
            c = costo_plan(cand, precios) if cand else float("inf")
            if c < mejor - 1e-9:
                plan, mejor, hubo_mejora = cand, c, True
            else:
                h.update(viejo)             # deshacer el giro
        if not hubo_mejora:
            break
    return plan
