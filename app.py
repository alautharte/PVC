import streamlit as st
import math
import pandas as pd
from datetime import date
import io
import os
from collections import Counter, defaultdict

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, PageBreak,
)
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.utils import ImageReader

try:
    import pulp
    HAY_PULP = True
except ImportError:          # si no está instalado, la app sigue con las heurísticas
    HAY_PULP = False

st.set_page_config(page_title="Presupuestador Cielorrasos PVC", page_icon="🏠", layout="wide")

HAB_COLORS = ["#2563EB","#16A34A","#DC2626","#9333EA","#D97706",
              "#0891B2","#DB2777","#65A30D","#EA580C","#7C3AED"]

NAVY       = colors.HexColor("#1B2A6B")
LIGHT_GRAY = colors.HexColor("#F3F4F6")
MID_GRAY   = colors.HexColor("#D1D5DB")
DARK_GRAY  = colors.HexColor("#374151")

# ── Constantes fijas ────────────────────────────────────────────────────────
PW              = 0.20   # ancho de placa — fijo 20 cm
LARGO_PERFIL    = 2.60
LARGO_MOLDURA   = 4.00
LARGO_H         = 4.00   # perfil H — 4 m
MIN_TRAMO_H     = 0.30   # tramo mínimo razonable al partir una tira con H
SEP_MAESTRAS    = 1.20
SEP_VELAS       = 1.00
SEP_MONTANTES   = 0.50
DIST_TARUGOS    = 0.50
TORNILLOS_T1_M2 = 12
TORNILLOS_T2_M2 =  5
EPS             = 1e-4   # 0.1 mm — tolerancia de comparación en la capa geométrica (metros, cuantizada a mm)
MAX_PATRONES    = 20000  # techo de patrones de corte que se enumeran antes de abortar el ILP

# Modelo de precisión: dos capas, cada una con su propio redondeo, documentadas.
#   1) Geometría (metros): toda dimensión se cuantiza a milímetros con Q(); las
#      comparaciones usan EPS < 0.5 mm, así que nunca hay ambigüedad de borde.
#   2) ILP (resolver_optimo / _patrones): opera en enteros de milímetro exactos
#      vía _mm()/_MM, sin float, por eso ahí no hace falta EPS.
def Q(x):
    """Cuantiza una dimensión en metros a milímetros (3 decimales)."""
    return round(x, 3)

# Avisos que la corrida actual quiere mostrarle al usuario (se resetea en cada
# rerun de Streamlit porque el módulo se re-ejecuta entero).
AVISOS = []

st.title("🏠 Presupuestador Cielorrasos PVC")
st.caption("Optimización global de corte — mezcla de largos, perfiles H, colores por habitación")

# ═══════════════════════════════════════════════════════════════════════════
# UTILIDADES
# ═══════════════════════════════════════════════════════════════════════════

def n_filas(dim):
    """Cantidad de filas de placa (20 cm) para cubrir 'dim' metros."""
    return math.ceil(round(dim / PW, 6))


def varillas_h(lineas, largo_linea):
    """Varillas de H de 4 m para 'lineas' líneas de unión de 'largo_linea' metros."""
    if lineas <= 0 or largo_linea <= 0:
        return 0
    return lineas * math.ceil(round(largo_linea / LARGO_H, 6))

# ═══════════════════════════════════════════════════════════════════════════
# LÓGICA DE PERFIL H
# ═══════════════════════════════════════════════════════════════════════════

def dividir_con_h(dim, lens):
    """
    Divide una tira más larga que la placa máxima en tramos que entren.
    Usa tramos del largo máximo y, si el último tramo queda muy corto
    (< MIN_TRAMO_H), reparte los dos últimos en partes iguales.
    Retorna: (segmentos: [float], uniones_por_tira: int)
    """
    max_L = max(lens)
    if dim <= max_L + EPS:
        return [dim], 0

    n_tramos  = math.ceil(round(dim / max_L, 6))
    segmentos = [float(max_L)] * (n_tramos - 1)
    resto     = Q(dim - max_L * (n_tramos - 1))

    if resto < MIN_TRAMO_H:
        ultimo        = Q(segmentos[-1] + resto)
        segmentos[-1] = Q(ultimo / 2)
        resto         = Q(ultimo - segmentos[-1])

    segmentos.append(resto)
    return segmentos, n_tramos - 1


def mejor_corte_h_opcional(dim, filas, hab_idx, todas_piezas_sin_hab, lens):
    """
    Para H opcional: prueba dividir dim en seg1+seg2 usando candidatos
    basados en sobrantes naturales del pool actual.
    Criterio de mejora: menos placas totales.
    Retorna (seg1, seg2, plan_mejorado) o None si ningún corte mejora.
    """
    piezas_base = [{"dim": dim, "hab_idx": hab_idx}] * filas
    plan_base   = elegir_mejor_plan(todas_piezas_sin_hab + piezas_base, lens)
    n_base      = len(plan_base)

    dims_pool = set(round(p["dim"], 3) for p in todas_piezas_sin_hab)
    candidatos = set()
    for L in lens:
        for d in dims_pool:
            sobra = Q(L - d)
            if 0.05 < sobra < dim - 0.05 and sobra <= max(lens):
                candidatos.add(sobra)
        candidatos.add(Q(dim / 2))

    mejor_plan = None
    mejor_n    = n_base

    for seg1 in sorted(candidatos):
        seg2 = Q(dim - seg1)
        if seg2 < 0.05 or seg2 > max(lens) + EPS:
            continue
        if seg1 > max(lens) + EPS:
            continue

        piezas_h = []
        for _ in range(filas):
            piezas_h.append({"dim": seg1, "hab_idx": hab_idx})
            piezas_h.append({"dim": seg2, "hab_idx": hab_idx})

        plan_h = elegir_mejor_plan(todas_piezas_sin_hab + piezas_h, lens)
        if plan_h and len(plan_h) < mejor_n:
            mejor_n    = len(plan_h)
            mejor_plan = (seg1, seg2, plan_h)

    return mejor_plan


def necesita_h(dim, lens):
    """True si la dimensión supera el largo máximo disponible."""
    return dim > max(lens) + EPS if lens else False

# ═══════════════════════════════════════════════════════════════════════════
# MOTOR DE CORTE GLOBAL (heurísticas — se usan como respaldo)
# ═══════════════════════════════════════════════════════════════════════════

def mejor_largo_para_pieza(d, lens):
    """Largo de placa con menor desperdicio para una pieza de dimensión d."""
    best_L, best_desp = None, float("inf")
    for L in lens:
        if L < d - EPS:
            continue
        n = math.floor(L / d) if d > 0 else 0
        if n == 0:
            continue
        desp = (L - n * d) / L
        if desp < best_desp:
            best_desp = desp
            best_L    = L
    return best_L


def _resolver_ffd(piezas, elegir_largo):
    """
    Motor único de bin-packing 1D First-Fit-Decreasing. Antes vivía duplicado
    en resolver_mixto y resolver_con_largo con una sola diferencia real: cómo
    se elige el largo de placa para una pieza que no entra en ningún
    sobrante abierto. Esa diferencia ahora es el parámetro 'elegir_largo'
    (dim -> L o None si no hay ningún largo que la contenga).
    Retorna None si alguna pieza no entra en ninguna placa disponible.
    """
    sorted_p  = sorted(piezas, key=lambda x: -x["dim"])
    sobrantes = []
    plan      = []
    for p in sorted_p:
        sobrantes.sort(key=lambda s: s["libre"])
        usado = False
        for s in sobrantes:
            if s["libre"] >= p["dim"] - EPS:
                bin_ = plan[s["plan_idx"]]
                bin_["cortes"].append({"dim": p["dim"], "hab_idx": p["hab_idx"]})
                s["libre"]    = max(0.0, s["libre"] - p["dim"])
                bin_["libre"] = s["libre"]
                usado = True
                break
        if not usado:
            L = elegir_largo(p["dim"])
            if L is None:
                return None
            libre = max(0.0, L - p["dim"])
            idx   = len(plan)
            plan.append({"largo_placa": L,
                         "cortes": [{"dim": p["dim"], "hab_idx": p["hab_idx"]}],
                         "libre": libre})
            sobrantes.append({"plan_idx": idx, "libre": libre})
    return plan


def resolver_mixto(piezas, lens):
    """
    Bin-packing 1D MIXTO: para cada pieza elige el largo de placa con
    menor desperdicio individual, reutilizando sobrantes entre piezas.
    Retorna None si alguna pieza no entra en ninguna placa (antes la salteaba
    en silencio y el resultado quedaba con desperdicio negativo).
    """
    if not piezas or not lens:
        return []
    return _resolver_ffd(piezas, lambda d: mejor_largo_para_pieza(d, lens))


def resolver_con_largo(piezas, L):
    """Bin-packing 1D con largo L fijo. Retorna None si alguna pieza no cabe."""
    if not piezas:
        return []
    return _resolver_ffd(piezas, lambda d: L if L >= d - EPS else None)


def elegir_mejor_plan(piezas, lens):
    """Evalúa cada largo fijo y elige el que minimiza el desperdicio global."""
    if not piezas or not lens:
        return []
    mejor_plan = None
    mejor_desp = float("inf")
    metros_netos = sum(p["dim"] for p in piezas)
    for L in lens:
        plan = resolver_con_largo(piezas, L)
        if plan is None:
            continue
        desp = sum(b["largo_placa"] for b in plan) - metros_netos
        if desp < mejor_desp:
            mejor_desp = desp
            mejor_plan = plan
    return mejor_plan or []

# ═══════════════════════════════════════════════════════════════════════════
# OPTIMIZADOR EXACTO (ILP por patrones de corte)
# ═══════════════════════════════════════════════════════════════════════════

_MM = 1000  # milímetros enteros: evita errores de redondeo tipo 5.9999


def _mm(x):
    return int(round(x * _MM))


class _PatronesExcedidos(Exception):
    """Señal interna: el catálogo de piezas generó más patrones que MAX_PATRONES.
    Se usa para abortar la enumeración temprano en vez de colgar la app; el
    llamador (resolver_optimo) la atrapa y cae al motor heurístico."""


def _patrones(L_mm, dims, demanda, kerf, tope_global):
    """
    Enumera patrones MAXIMALES de corte para una placa de L_mm.
    Con kerf, n piezas ocupan n*d + (n-1)*kerf  ->  capacidad efectiva L+kerf.

    tope_global es un contador mutable ([int]) compartido entre TODOS los
    largos de placa de esta corrida de resolver_optimo: si el catálogo tiene
    piezas muy chicas (remates de 20 cm) frente a placas largas (6 m), la
    cantidad de combinaciones puede crecer exponencialmente. En vez de dejar
    que eso cuelgue el proceso, se aborta apenas se supera MAX_PATRONES y el
    optimizador exacto cede el turno a las heurísticas (que siempre corren,
    ver candidatos_plan más abajo).
    """
    dims = sorted(dims, reverse=True)
    cap  = L_mm + kerf
    pats = []

    def rec(i, libre, cuenta):
        if i == len(dims):
            maximal = all(cuenta[j] >= demanda[d] or d + kerf > libre
                          for j, d in enumerate(dims))
            if maximal and any(cuenta):
                pats.append({d: c for d, c in zip(dims, cuenta) if c})
                tope_global[0] += 1
                if tope_global[0] > MAX_PATRONES:
                    raise _PatronesExcedidos()
            return
        d    = dims[i]
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


def clave_plan(plan, precios=None):
    """Criterio de comparación entre planes: primero costo ($ o metros),
    a igual costo, menos placas."""
    if not plan:
        return (float("inf"), float("inf"))
    return (round(costo_plan(plan, precios), 6), len(plan))


def resolver_optimo(piezas, lens, precios=None, kerf=0.0, limite_seg=15):
    """
    Cutting stock óptimo por patrones. Minimiza $ si hay precios para todos
    los largos disponibles; si no, metros comprados. Devuelve plan o None.
    """
    if not HAY_PULP or not piezas or not lens:
        return None

    kerf_mm = _mm(kerf)
    demanda = Counter(_mm(p["dim"]) for p in piezas)
    dims    = list(demanda)
    if max(dims) > _mm(max(lens)):
        return None  # hay piezas que no entran en ninguna placa

    usar_precio = bool(precios) and all(precios.get(L, 0) > 0 for L in lens)

    contador = [0]   # compartido entre todos los L: ver _PatronesExcedidos
    columnas = []
    try:
        for L in lens:
            for pat in _patrones(_mm(L), dims, demanda, kerf_mm, contador):
                columnas.append((L, pat))
    except _PatronesExcedidos:
        AVISOS.append(
            f"El catálogo de piezas es demasiado grande/fino para el optimizador "
            f"exacto (más de {MAX_PATRONES} patrones de corte posibles). Se usó "
            f"el motor heurístico para esta combinación de largos.")
        return None

    prob = pulp.LpProblem("corte_pvc", pulp.LpMinimize)
    x = [pulp.LpVariable(f"x{k}", lowBound=0, cat="Integer") for k in range(len(columnas))]

    def costo(L):
        base = precios[L] if usar_precio else L
        return base * 1000 + 1   # a igual costo, preferir menos placas

    prob += pulp.lpSum(costo(L) * x[k] for k, (L, _) in enumerate(columnas))
    for d in dims:
        prob += pulp.lpSum(pat.get(d, 0) * x[k]
                           for k, (_, pat) in enumerate(columnas)) >= demanda[d]

    try:
        prob.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=limite_seg))
    except pulp.PulpSolverError as e:
        AVISOS.append(f"El solver CBC falló al resolver el corte ({e}); se usó heurística.")
        return None
    estado = pulp.LpStatus[prob.status]
    if estado in ("Infeasible", "Unbounded") or any(v.value() is None for v in x):
        return None
    if estado != "Optimal":
        # CBC llegó al límite de tiempo sin poder DEMOSTRAR optimalidad; el
        # plan que devuelve puede ser subóptimo. Igual se usa como candidato
        # (compite contra las heurísticas más abajo, nunca da peor resultado
        # que ellas), pero se avisa porque puede no ser el mínimo teórico.
        AVISOS.append(
            f"El optimizador exacto no llegó a demostrar que el corte es óptimo "
            f"dentro de {limite_seg}s (estado: {estado}). El plan mostrado es "
            f"válido y se comparó contra las heurísticas, pero podría no ser el "
            f"mínimo absoluto de placas. Subí el límite de tiempo en el código "
            f"(resolver_optimo, limite_seg) si necesitás la prueba de óptimo.")

    placas = []
    for k, (L, pat) in enumerate(columnas):
        n = int(round(x[k].value() or 0))
        for _ in range(n):
            placas.append({"L": L, "cnt": dict(pat)})

    producido = Counter()
    for p in placas:
        producido.update(p["cnt"])
    for d in dims:
        exceso = producido[d] - demanda[d]
        for p in sorted(placas, key=lambda p: -p["cnt"].get(d, 0)):
            if exceso <= 0:
                break
            quita = min(exceso, p["cnt"].get(d, 0))
            p["cnt"][d] = p["cnt"].get(d, 0) - quita
            exceso -= quita
    placas = [p for p in placas if sum(p["cnt"].values()) > 0]

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

    plan.sort(key=lambda b: (b["largo_placa"], [-c["dim"] for c in b["cortes"]]))
    return plan


def mejorar_orientaciones(hab_info, lens, pw, precios=None, kerf=0.0):
    """
    Búsqueda local de orientación con el optimizador GLOBAL.
    Para cada habitación rectangular sin sentido fijo y sin perfil H, prueba
    girar las placas y se queda con el giro si baja el costo total de la obra.
    """
    def todas():
        return [p for h in hab_info for p in h["piezas"]]

    plan = resolver_optimo(todas(), lens, precios, kerf)
    if plan is None:
        return None
    mejor = clave_plan(plan, precios)

    for _ in range(2):
        hubo_mejora = False
        for h in hab_info:
            if h.get("tipo") == "l" or h.get("fijo") or h.get("n_h", 0) > 0:
                continue
            if h["orient"] == "largo":
                n_or, n_dim, n_fil, n_tr = "ancho", h["ancho"], n_filas(h["largo"]), h["largo"]
            else:
                n_or, n_dim, n_fil, n_tr = "largo", h["largo"], n_filas(h["ancho"]), h["ancho"]
            if n_dim > max(lens) + EPS:
                continue
            viejo = {k: h[k] for k in ("orient", "dim_pieza", "filas", "piezas",
                                        "segmentos", "transv")}
            h.update(orient=n_or, dim_pieza=n_dim, filas=n_fil, segmentos=[n_dim],
                     transv=n_tr, piezas=[{"dim": n_dim, "hab_idx": h["idx"]}] * n_fil)
            cand = resolver_optimo(todas(), lens, precios, kerf)
            c = clave_plan(cand, precios)
            if c < mejor:
                plan, mejor, hubo_mejora = cand, c, True
            else:
                h.update(viejo)
        if not hubo_mejora:
            break
    return plan

# ═══════════════════════════════════════════════════════════════════════════
# AMBIENTES EN L
# ═══════════════════════════════════════════════════════════════════════════

def _grupos_l(lt, at, lr, ar, orient):
    """
    Grupos de tiras (dimensión, cantidad) de una L. El recorte (lr × ar) es el
    pedazo que FALTA, en cualquier esquina: la esquina no cambia las cantidades.

    Las filas se colocan ARRANCANDO POR EL BRAZO COMPLETO:
      - filas largas = ceil(brazo completo / 0,20). La fila que cruza el borde
        del recorte es larga (tiene que cubrir el brazo completo).
      - filas cortas = lo que falta hasta la pared del recorte, redondeado
        hacia arriba. La última fila corta es el borde (se recorta a lo ancho).
    Así se usan la menor cantidad posible de filas largas con el mismo total.

    orient "ancho": placas en dirección del ANCHO, las filas avanzan por el largo.
        largas = ancho_total,                cortas = ancho_total - ancho_recorte
    orient "largo": placas en dirección del LARGO, las filas avanzan por el ancho.
        largas = largo_total,                cortas = largo_total - largo_recorte

    Ej.: 3,00 × 3,25 con recorte 0,80 × 1,56, placas a lo largo:
        largas = ceil(1,69 / 0,20) = 9 de 3,00 m
        cortas = ceil((3,25 - 1,80) / 0,20) = ceil(7,25) = 8 de 2,20 m
    """
    if orient == "ancho":
        total  = n_filas(lt)
        largas = min(n_filas(Q(lt - lr)), total)
        return [(Q(at), largas), (Q(at - ar), total - largas)]
    total  = n_filas(at)
    largas = min(n_filas(Q(at - ar)), total)
    return [(Q(lt), largas), (Q(lt - lr), total - largas)]


def _expandir_grupos(grupos, idx, lens, usar_h):
    """Convierte grupos en piezas, partiendo con H las tiras que no entran."""
    piezas, info = [], []
    for dim, n in grupos:
        if n <= 0 or dim <= 0:
            continue
        if usar_h and necesita_h(dim, lens):
            segs, k = dividir_con_h(dim, lens)
        else:
            segs, k = [dim], 0
        for _ in range(n):
            piezas += [{"dim": s, "hab_idx": idx} for s in segs]
        info.append({"dim": dim, "filas": n, "segs": segs, "lineas_h": k,
                     # la línea de unión cubre solo las filas de este grupo
                     "varillas_h": varillas_h(k, n * PW)})
    return piezas, info


def calcular_piezas_l(lt, at, lr, ar, idx, lens, otras_piezas=None,
                      usar_h=True, precios=None, forzar=None):
    """
    Evalúa las dos orientaciones de una L y elige:
      0. Si 'forzar' es "largo" o "ancho" -> usa ese sentido sí o sí (sentido fijo)
      1. Si una entra sin H y la otra no -> la que no necesita H
      2. Si ambas (o ninguna) necesitan H -> la de menor costo en contexto global
    Retorna dict con orient, grupos, piezas, info, n_h, varillas_h.
    """
    otras  = otras_piezas or []
    max_L  = max(lens)
    opciones = []
    for orient in ("ancho", "largo"):
        grupos         = _grupos_l(lt, at, lr, ar, orient)
        piezas, info   = _expandir_grupos(grupos, idx, lens, usar_h)
        opciones.append({
            "orient":     orient,
            "grupos":     grupos,
            "piezas":     piezas,
            "info":       info,
            "necesita_h": any(n > 0 and necesita_h(d, lens) for d, n in grupos),
            "entra":      all(p["dim"] <= max_L + EPS for p in piezas),
            "n_h":        sum(g["lineas_h"] for g in info),
            "varillas_h": sum(g["varillas_h"] for g in info),
        })

    if forzar in ("largo", "ancho"):
        return next(o for o in opciones if o["orient"] == forzar)

    entran = [o for o in opciones if o["entra"]]
    if not entran:
        return opciones[0]   # solo pasa sin H; el control global muestra el error

    sin_h     = [o for o in entran if not o["necesita_h"]]
    candidatas = sin_h or entran
    if len(candidatas) == 1:
        return candidatas[0]

    def evaluar(o):
        plan = (resolver_optimo(otras + o["piezas"], lens, precios)
                or resolver_mixto(otras + o["piezas"], lens))
        return clave_plan(plan, precios) + (o["varillas_h"],)

    return min(candidatas, key=evaluar)


def diagrama_l_svg(lt, at, lr, ar, color, esquina="inf_izq"):
    """
    SVG de la L con medidas. El recorte (lr × ar) siempre está en la esquina elegida.
    """
    scale = 110 / max(lt, at)
    pad   = 36
    W_svg = int(lt * scale) + pad * 2 + 20
    H_svg = int(at * scale) + pad * 2
    x0, y0 = pad, pad
    wt  = int(lt * scale)
    ht  = int(at * scale)
    wlr = int(lr * scale)
    har = int(ar * scale)
    c   = color

    if esquina == "inf_izq":
        pts = (f"{x0},{y0} {x0+wt},{y0} {x0+wt},{y0+ht} "
               f"{x0+wlr},{y0+ht} {x0+wlr},{y0+ht-har} {x0},{y0+ht-har}")
        dim_lines = (
            _cota_h(x0, y0-8, x0+wt, y0-8, lt, c, "above") +
            _cota_v(x0+wt+4, y0, x0+wt+4, y0+ht, at, c, "right") +
            _cota_h(x0, y0+ht+6, x0+wlr, y0+ht+6, lr, c, "below") +
            _cota_v(x0+wlr+4, y0+ht-har, x0+wlr+4, y0+ht, ar, c, "right")
        )
    elif esquina == "inf_der":
        pts = (f"{x0},{y0} {x0+wt},{y0} {x0+wt},{y0+ht-har} "
               f"{x0+wt-wlr},{y0+ht-har} {x0+wt-wlr},{y0+ht} {x0},{y0+ht}")
        dim_lines = (
            _cota_h(x0, y0-8, x0+wt, y0-8, lt, c, "above") +
            _cota_v(x0-4, y0, x0-4, y0+ht, at, c, "left") +
            _cota_h(x0+wt-wlr, y0+ht+6, x0+wt, y0+ht+6, lr, c, "below") +
            _cota_v(x0+wt-wlr-4, y0+ht-har, x0+wt-wlr-4, y0+ht, ar, c, "left")
        )
    elif esquina == "sup_izq":
        pts = (f"{x0+wlr},{y0} {x0+wt},{y0} {x0+wt},{y0+ht} "
               f"{x0},{y0+ht} {x0},{y0+har} {x0+wlr},{y0+har}")
        dim_lines = (
            _cota_h(x0, y0+ht+6, x0+wt, y0+ht+6, lt, c, "below") +
            _cota_v(x0+wt+4, y0, x0+wt+4, y0+ht, at, c, "right") +
            _cota_h(x0, y0-8, x0+wlr, y0-8, lr, c, "above") +
            _cota_v(x0-4, y0, x0-4, y0+har, ar, c, "left")
        )
    else:  # sup_der
        pts = (f"{x0},{y0} {x0+wt-wlr},{y0} {x0+wt-wlr},{y0+har} "
               f"{x0+wt},{y0+har} {x0+wt},{y0+ht} {x0},{y0+ht}")
        dim_lines = (
            _cota_h(x0, y0+ht+6, x0+wt, y0+ht+6, lt, c, "below") +
            _cota_v(x0-4, y0, x0-4, y0+ht, at, c, "left") +
            _cota_h(x0+wt-wlr, y0-8, x0+wt, y0-8, lr, c, "above") +
            _cota_v(x0+wt+4, y0, x0+wt+4, y0+har, ar, c, "right")
        )

    return (
        '<svg width="{}" height="{}" xmlns="http://www.w3.org/2000/svg">'
        '<polygon points="{}" fill="{}22" stroke="{}" stroke-width="2"/>'
        '{}</svg>'
    ).format(W_svg, H_svg, pts, c, c, dim_lines)


def _cota_h(x1, y1, x2, y2, val, color, pos):
    """Línea horizontal con etiqueta."""
    ym = (y1+y2)//2 if y1==y2 else y1
    ty = ym - 3 if pos == "above" else ym + 11
    return (
        '<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="{}" stroke-width="1"/>'.format(x1,y1,x2,y2,color) +
        '<text x="{}" y="{}" fill="{}" font-size="10" text-anchor="middle">{}m</text>'.format(
            (x1+x2)//2, ty, color, val)
    )

def _cota_v(x1, y1, x2, y2, val, color, pos):
    """Línea vertical con etiqueta."""
    tx = x1 + 14 if pos == "right" else x1 - 4
    anchor = "start" if pos == "right" else "end"
    return (
        '<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="{}" stroke-width="1"/>'.format(x1,y1,x2,y2,color) +
        '<text x="{}" y="{}" fill="{}" font-size="10" text-anchor="{}" '
        'transform="rotate(-90,{},{})">{}m</text>'.format(
            tx, (y1+y2)//2, color, anchor, tx, (y1+y2)//2, val)
    )


def orientacion_optima(r, idx, lens_efectivos):
    """
    Elige orientación inicial minimizando costo (evaluación por habitación).
    Retorna: orient, dim_pieza, filas, piezas, segmentos, uniones_por_tira
    (uniones_por_tira = cantidad de LÍNEAS de perfil H en la habitación).
      1. Si una orientación entra sin H y la otra no -> la que no necesita H
      2. Si ambas entran sin H -> la de menor costo
      3. Si ninguna entra sin H -> usar H en la que genere menos costo
    """
    filas_largo = n_filas(r["ancho"])
    filas_ancho = n_filas(r["largo"])

    largo_necesita_h = necesita_h(r["largo"], lens_efectivos)
    ancho_necesita_h = necesita_h(r["ancho"], lens_efectivos)

    def piezas_simples(dim, filas, hab_idx):
        return [{"dim": dim, "hab_idx": hab_idx}] * filas

    def piezas_con_h(dim, filas, hab_idx, lens):
        segs, _ = dividir_con_h(dim, lens)
        return [{"dim": s, "hab_idx": hab_idx} for s in segs] * filas

    if r["fijo"]:
        if largo_necesita_h:
            segs, n_h = dividir_con_h(r["largo"], lens_efectivos)
            piezas = piezas_con_h(r["largo"], filas_largo, idx, lens_efectivos)
        else:
            segs, n_h = [r["largo"]], 0
            piezas = piezas_simples(r["largo"], filas_largo, idx)
        return "largo", r["largo"], filas_largo, piezas, segs, n_h

    if not largo_necesita_h and ancho_necesita_h:
        return "largo", r["largo"], filas_largo, piezas_simples(r["largo"], filas_largo, idx), [r["largo"]], 0

    if largo_necesita_h and not ancho_necesita_h:
        return "ancho", r["ancho"], filas_ancho, piezas_simples(r["ancho"], filas_ancho, idx), [r["ancho"]], 0

    if not largo_necesita_h and not ancho_necesita_h:
        p_largo = piezas_simples(r["largo"], filas_largo, idx)
        p_ancho = piezas_simples(r["ancho"], filas_ancho, idx)
        c_l = clave_plan(resolver_optimo(p_largo, lens_efectivos) or elegir_mejor_plan(p_largo, lens_efectivos))
        c_a = clave_plan(resolver_optimo(p_ancho, lens_efectivos) or elegir_mejor_plan(p_ancho, lens_efectivos))
        if c_l <= c_a:
            return "largo", r["largo"], filas_largo, p_largo, [r["largo"]], 0
        return "ancho", r["ancho"], filas_ancho, p_ancho, [r["ancho"]], 0

    p_largo = piezas_con_h(r["largo"], filas_largo, idx, lens_efectivos)
    p_ancho = piezas_con_h(r["ancho"], filas_ancho, idx, lens_efectivos)
    c_l = clave_plan(resolver_optimo(p_largo, lens_efectivos) or elegir_mejor_plan(p_largo, lens_efectivos))
    c_a = clave_plan(resolver_optimo(p_ancho, lens_efectivos) or elegir_mejor_plan(p_ancho, lens_efectivos))
    if c_l <= c_a:
        segs, n_h = dividir_con_h(r["largo"], lens_efectivos)
        return "largo", r["largo"], filas_largo, p_largo, segs, n_h
    segs, n_h = dividir_con_h(r["ancho"], lens_efectivos)
    return "ancho", r["ancho"], filas_ancho, p_ancho, segs, n_h

# ═══════════════════════════════════════════════════════════════════════════
# ESTRUCTURA
# ═══════════════════════════════════════════════════════════════════════════

def hab_largo(h):
    return h.get("largo_total", h.get("largo", 0))

def hab_ancho(h):
    return h.get("ancho_total", h.get("ancho", 0))

def hab_area(h):
    """Área real. En L: rectángulo total menos el recorte (lo que falta)."""
    if h.get("tipo") == "l":
        return Q(h["largo_total"] * h["ancho_total"]
                - h["largo_reducido"] * h["ancho_reducido"])
    return Q(h.get("largo", 0) * h.get("ancho", 0))

def hab_perim(h):
    """Perímetro. En una L ortogonal es igual al del rectángulo que la contiene."""
    if h.get("tipo") == "l":
        return Q(2 * (h["largo_total"] + h["ancho_total"]))
    return Q(2 * (h.get("largo", 0) + h.get("ancho", 0)))

def calcular_estructura(largo, ancho, altura, orientacion,
                        area_real=None, perim_real=None):
    """Calcula estructura. Para L, pasar area_real y perim_real."""
    if orientacion == "largo":
        dim_paralela, dim_transversal = largo, ancho
    else:
        dim_paralela, dim_transversal = ancho, largo

    P  = perim_real if perim_real is not None else (largo + ancho) * 2
    SP = math.ceil(P / LARGO_PERFIL)

    lineas_maestras  = max(0, math.ceil(dim_transversal / SEP_MAESTRAS) - 1)
    metros_maestras  = lineas_maestras * dim_paralela
    soleras_maestras = math.ceil(metros_maestras / LARGO_PERFIL) if metros_maestras > 0 else 0

    velas_por_linea = math.ceil(dim_paralela / SEP_VELAS) + 1
    total_velas     = velas_por_linea * lineas_maestras
    soleras_velas   = math.ceil(total_velas * altura / LARGO_PERFIL) if total_velas > 0 else 0

    total_soleras   = SP + soleras_maestras + soleras_velas

    lineas_montantes = math.ceil(dim_paralela / SEP_MONTANTES) + 1
    total_montantes  = math.ceil(lineas_montantes * dim_transversal / LARGO_PERFIL)

    total_molduras = math.ceil(P / LARGO_MOLDURA)
    total_tarugos  = math.ceil(P / DIST_TARUGOS) + total_velas

    area     = area_real if area_real is not None else largo * ancho
    total_t1 = math.ceil(area * TORNILLOS_T1_M2)
    total_t2 = math.ceil(area * TORNILLOS_T2_M2)

    return {
        "perimetro": round(P, 2),
        "total_soleras": total_soleras,
        "total_montantes": total_montantes,
        "total_molduras": total_molduras,
        "total_tarugos_n8": total_tarugos,
        "total_t1": total_t1,
        "total_t2": total_t2,
    }


def texto_cortes_h(h):
    """Texto legible de cómo se parten las tiras con H en una habitación."""
    if h.get("tipo") == "l":
        partes = [f"{g['dim']}m → " + " + ".join(f"{s}m" for s in g["segs"])
                  + f" ({g['filas']} filas)"
                  for g in h.get("grupos_h", []) if g["lineas_h"] > 0]
        return " | ".join(partes)
    return " + ".join(f"{s}m" for s in h["segmentos"])

# ═══════════════════════════════════════════════════════════════════════════
# PDF
# ═══════════════════════════════════════════════════════════════════════════

def logo_imagen():
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")
        if not os.path.exists(p):
            return None
        with open(p, "rb") as f:
            return ImageReader(io.BytesIO(f.read()))
    except (OSError, IOError):
        # logo.png ausente, sin permisos de lectura, o corrupto: el PDF sigue
        # generándose igual, solo cae al título de texto en vez del logo.
        return None


def generar_pdf(habs, hab_info, plan, todas_piezas,
                total_area, tot_sol, tot_mon, tot_mol, tot_tar,
                tot_t1, tot_t2, tot_h_perfiles, tot_h_varillas,
                m2_comprados, m2_desperdiciados, pct_desp,
                conteo, costo_total, estructuras, descripcion):

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=1.8*cm, rightMargin=1.8*cm,
                            topMargin=1.5*cm,  bottomMargin=2.2*cm)

    s_title  = ParagraphStyle("t", fontSize=14, textColor=NAVY, fontName="Helvetica-Bold", spaceAfter=2)
    s_sub    = ParagraphStyle("s", fontSize=9,  textColor=NAVY, fontName="Helvetica-Bold", spaceBefore=8, spaceAfter=3)
    s_body   = ParagraphStyle("b", fontSize=8,  textColor=DARK_GRAY, fontName="Helvetica", spaceAfter=2)
    s_footer = ParagraphStyle("f", fontSize=7.5, textColor=DARK_GRAY, fontName="Helvetica-Oblique", alignment=TA_CENTER)

    story = []
    W     = A4[0] - 3.6*cm
    fecha = date.today().strftime("%d/%m/%Y")

    logo_img = logo_imagen()
    if logo_img:
        from reportlab.platypus.flowables import Image as FImage
        logo_cell = FImage(logo_img, width=4.5*cm, height=1.8*cm)
    else:
        logo_cell = Paragraph("<b>LAUTHARTE MATERIALES</b>", s_title)

    t_head = Table([[logo_cell, Paragraph(f"<b>Fecha:</b> {fecha}", s_body)]],
                   colWidths=[W*0.65, W*0.35])
    t_head.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE"),("ALIGN",(1,0),(1,0),"RIGHT")]))
    story += [t_head, HRFlowable(width="100%", thickness=1.5, color=NAVY, spaceAfter=5)]

    if descripcion.strip():
        story.append(Paragraph(f"<b>Obra / Descripción:</b> {descripcion.strip()}", s_body))
        story.append(Spacer(1,3))

    story.append(Paragraph("RESUMEN DE MATERIALES", s_sub))
    desc_mix = "  |  ".join(f"{conteo[l]} × {l}m" for l in [4,5,6] if conteo.get(l,0)>0)
    h_txt = (f"{tot_h_varillas} varillas 4m ({tot_h_perfiles} uniones)"
             if tot_h_varillas > 0 else "No se requieren")

    res = [
        ["Área total","Placas PVC","m² comprados","m² desperdicio","Desperdicio %"],
        [f"{total_area:.2f} m²", desc_mix, f"{m2_comprados:.2f} m²",
         f"{m2_desperdiciados:.2f} m²", f"{pct_desp:.1f}%"],
        ["Soleras (2.6m)","Montantes (2.6m)","Molduras PVC (4m)","Tarugos N°8","Perfiles H"],
        [f"{tot_sol} un.", f"{tot_mon} un.", f"{tot_mol} un.", f"{tot_tar} un.", h_txt],
        ["T1 placas","T2 perfiles","","",""],
        [f"{tot_t1} un.", f"{tot_t2} un.","","",""],
    ]
    if costo_total > 0:
        res[0].append("Costo"); res[1].append(f"${costo_total:,.0f}")
        res[2].append(""); res[3].append("")
        res[4].append(""); res[5].append("")

    cn = len(res[0])
    t_res = Table(res, colWidths=[W/cn]*cn)
    t_res.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,0), NAVY), ("BACKGROUND",(0,2),(-1,2), NAVY),
        ("BACKGROUND",    (0,4),(-1,4), NAVY),
        ("TEXTCOLOR",     (0,0),(-1,0), colors.white), ("TEXTCOLOR",(0,2),(-1,2),colors.white),
        ("TEXTCOLOR",     (0,4),(-1,4), colors.white),
        ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"), ("FONTNAME",(0,2),(-1,2),"Helvetica-Bold"),
        ("FONTNAME",      (0,4),(-1,4), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0),(-1,-1), 7.5), ("ALIGN",(0,0),(-1,-1),"CENTER"),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
        ("ROWBACKGROUNDS",(0,1),(-1,1), [LIGHT_GRAY]), ("ROWBACKGROUNDS",(0,3),(-1,3),[LIGHT_GRAY]),
        ("ROWBACKGROUNDS",(0,5),(-1,5), [LIGHT_GRAY]),
        ("GRID",          (0,0),(-1,-1), 0.4, MID_GRAY),
        ("TOPPADDING",    (0,0),(-1,-1), 4), ("BOTTOMPADDING",(0,0),(-1,-1),4),
    ]))
    story += [t_res, Spacer(1,5)]

    story.append(Paragraph("DETALLE DE ESTRUCTURA POR HABITACIÓN", s_sub))
    eh = ["Habitación","Dim.","Alt.","Soleras","Montantes","Molduras","Tarugos","Perf. H","T1","T2"]
    erows = [eh]
    for hi in hab_info:
        e = estructuras[hi["idx"]]
        erows.append([hi["nombre"], f"{hab_largo(hi)}×{hab_ancho(hi)}m",
                      f"{hi.get('altura',0.30):.2f}m",
                      str(e["total_soleras"]), str(e["total_montantes"]),
                      str(e["total_molduras"]), str(e["total_tarugos_n8"]),
                      f"{hi['varillas_h']} var." if hi["n_h"]>0 else "—",
                      str(e["total_t1"]), str(e["total_t2"])])
    ew = [W*0.15,W*0.10,W*0.07,W*0.08,W*0.09,W*0.09,W*0.09,W*0.09,W*0.08,W*0.08]
    t_est = Table(erows, colWidths=ew, repeatRows=1)
    t_est.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),NAVY), ("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"), ("FONTSIZE",(0,0),(-1,-1),7),
        ("ALIGN",(0,0),(-1,-1),"CENTER"), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,LIGHT_GRAY]),
        ("GRID",(0,0),(-1,-1),0.4,MID_GRAY),
        ("TOPPADDING",(0,0),(-1,-1),3), ("BOTTOMPADDING",(0,0),(-1,-1),3),
    ]))
    story += [t_est, Spacer(1,5)]

    story.append(Paragraph("DETALLE DE PLACAS PVC POR HABITACIÓN", s_sub))
    s_ph = ParagraphStyle("ph", fontSize=7.5, textColor=colors.white,
                          fontName="Helvetica-Bold", leading=9, alignment=TA_CENTER)
    def _ph(txt):
        return Paragraph(txt.replace(" ", "<br/>", 1), s_ph)
    ph = [_ph("Habitación"), _ph("Área (m²)"), _ph("Dirección"), _ph("Medida placa"),
          _ph("Cantidad placas"), _ph("Perím."), _ph("Uniones H"), _ph("Varillas H (4m)")]
    prows = [ph]
    for h in hab_info:
        dir_ = ("→ largo" if h["orient"]=="largo" else "↓ ancho")+(" (fijo)" if h.get("fijo") else "")
        prows.append([h["nombre"], f"{hab_area(h):.2f}", dir_,
                      f"{h['dim_pieza']}m", str(h["filas"]), f"{hab_perim(h):.1f}",
                      str(h["n_h"]) if h["n_h"]>0 else "—",
                      str(h["varillas_h"]) if h["n_h"]>0 else "—"])
    pw2 = [W*0.16,W*0.09,W*0.15,W*0.11,W*0.10,W*0.09,W*0.12,W*0.18]
    t_plac = Table(prows, colWidths=pw2, repeatRows=1)
    t_plac.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),NAVY), ("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"), ("FONTSIZE",(0,1),(-1,-1),7.5),
        ("ALIGN",(0,0),(-1,-1),"CENTER"), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,LIGHT_GRAY]),
        ("GRID",(0,0),(-1,-1),0.4,MID_GRAY),
        ("TOPPADDING",(0,0),(-1,-1),4), ("BOTTOMPADDING",(0,0),(-1,-1),4),
    ]))
    story += [t_plac, Spacer(1,5)]

    story.append(PageBreak())
    story.append(Paragraph("PLAN DE CORTE — INSTRUCCIONES PASO A PASO", s_sub))
    story.append(Paragraph("Cada fila es una placa física.", s_body))
    story.append(Spacer(1,3))

    s_cell   = ParagraphStyle("cell",  fontSize=7.5, textColor=DARK_GRAY,
                               fontName="Helvetica",      leading=10)
    s_cell_h = ParagraphStyle("cellh", fontSize=7.5, textColor=colors.white,
                               fontName="Helvetica-Bold", leading=10)

    for L in [4,5,6]:
        bins_L = [b for b in plan if b["largo_placa"]==L]
        if not bins_L:
            continue
        story.append(Paragraph(f"Placas de {L} metros — {len(bins_L)} unidades", s_sub))

        crows = [[
            Paragraph("#", s_cell_h),
            Paragraph(f"Placa {L}m", s_cell_h),
            Paragraph("Instruccion de corte", s_cell_h),
            Paragraph("Sobrante", s_cell_h),
        ]]
        for idx, bin_ in enumerate(bins_L, 1):
            suma  = sum(c["dim"] for c in bin_["cortes"])
            libre = bin_["libre"]
            partes_html = "<br/>".join(
                f"{c['dim']}m -> {habs[c['hab_idx']]['nombre']}"
                for c in bin_["cortes"]
            )
            libre_str = f"{libre:.3f}m" if libre > 0.005 else "-"
            crows.append([
                Paragraph(str(idx),            s_cell),
                Paragraph(f"{suma:.2f}m/{L}m", s_cell),
                Paragraph(partes_html,          s_cell),
                Paragraph(libre_str,            s_cell),
            ])

        t_c = Table(crows, colWidths=[W*0.06, W*0.14, W*0.62, W*0.18], repeatRows=1)
        t_c.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,0),  NAVY),
            ("FONTSIZE",      (0,0),(-1,-1), 7.5),
            ("ALIGN",         (0,0),(1,-1),  "CENTER"),
            ("ALIGN",         (2,1),(2,-1),  "LEFT"),
            ("ALIGN",         (3,1),(3,-1),  "CENTER"),
            ("VALIGN",        (0,0),(-1,-1), "TOP"),
            ("ROWBACKGROUNDS",(0,1),(-1,-1), [colors.white, LIGHT_GRAY]),
            ("GRID",          (0,0),(-1,-1), 0.4, MID_GRAY),
            ("TOPPADDING",    (0,0),(-1,-1), 4),
            ("BOTTOMPADDING", (0,0),(-1,-1), 4),
            ("LEFTPADDING",   (2,0),(2,-1),  4),
        ]))
        story += [t_c, Spacer(1,4)]

    story += [Spacer(1,10), HRFlowable(width="100%", thickness=0.8, color=MID_GRAY, spaceAfter=6)]
    t_firma = Table([[
        Paragraph(f"Este plan de corte fue generado el {fecha}. "
                  "El cliente recibió y aceptó las instrucciones.", s_footer),
        Paragraph("Firma del cliente: ____________________________", s_footer),
    ]], colWidths=[W*0.6,W*0.4])
    t_firma.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"BOTTOM"),("ALIGN",(1,0),(1,0),"RIGHT")]))
    story.append(t_firma)

    doc.build(story)
    buf.seek(0)
    return buf.read()

# ═══════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ═══════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.header("⚙️ Configuración")

    st.subheader("Placas PVC disponibles")
    usar_4 = st.checkbox("Placa 4 m", value=True)
    usar_5 = st.checkbox("Placa 5 m", value=True)
    usar_6 = st.checkbox("Placa 6 m", value=True)
    lens   = [l for l,u in [(4,usar_4),(5,usar_5),(6,usar_6)] if u]
    st.caption(f"Ancho placa: **20 cm** (fijo)  |  Perfil H: **{LARGO_H:.0f} m**")

    usar_h = st.checkbox("🔗 Usar perfil H (unión de placas)", value=True,
                         help="Activa el cálculo de perfiles H para ambientes donde la dimensión supera el largo de placa disponible")

    st.subheader("Precios (opcional)")
    p4       = st.number_input("Placa 4 m ($ / un)",            value=0.0, step=100.0, format="%.0f")
    p5       = st.number_input("Placa 5 m ($ / un)",            value=0.0, step=100.0, format="%.0f")
    p6       = st.number_input("Placa 6 m ($ / un)",            value=0.0, step=100.0, format="%.0f")
    p_h      = st.number_input("Perfil H ($ / un)",             value=0.0, step=100.0, format="%.0f")
    pperim   = st.number_input("Moldura PVC ($ / un)",           value=0.0, step=100.0, format="%.0f")
    psol     = st.number_input("Solera ($ / un)",                value=0.0, step=100.0, format="%.0f")
    pmont    = st.number_input("Montante ($ / un)",              value=0.0, step=100.0, format="%.0f")
    ptarug   = st.number_input("Tarugo N°8 ($ / un)",            value=0.0, step=10.0,  format="%.0f")
    ptorn_t1 = st.number_input("Tornillo T1 — placas ($ / un)", value=0.0, step=10.0,  format="%.0f")
    ptorn_t2 = st.number_input("Tornillo T2 — perfiles ($ / un)",value=0.0,step=10.0,  format="%.0f")
    precios  = {4:p4, 5:p5, 6:p6}

    if not HAY_PULP:
        st.warning("Optimizador exacto no disponible (falta `pulp` en requirements.txt). "
                   "Se usa el motor heurístico.")

# ═══════════════════════════════════════════════════════════════════════════
# HABITACIONES
# ═══════════════════════════════════════════════════════════════════════════

st.subheader("📐 Habitaciones")

if "habitaciones" not in st.session_state:
    st.session_state.habitaciones = [
        {"hid":1,"nombre":"Habitación 1","largo":3.5,"ancho":4.0,"altura":0.30,"fijo":False,"forzar_h":False},
        {"hid":2,"nombre":"Habitación 2","largo":2.5,"ancho":3.0,"altura":0.30,"fijo":False,"forzar_h":False},
    ]
    st.session_state.hid_counter = 3

# hid_counter es la única fuente de verdad para asignar IDs de habitación:
# se inicializa UNA vez acá (nunca dentro de _next_hid) y de ahí en más solo
# se lee y se incrementa. Streamlit ejecuta los callbacks de los botones
# (agregar/agregar_l/eliminar) de forma síncrona y uno a la vez antes de
# volver a correr el script de arriba a abajo, así que no hay dos clics
# modificando este contador al mismo tiempo dentro de una misma sesión.
if "hid_counter" not in st.session_state:
    st.session_state.hid_counter = 1
for _h in st.session_state.habitaciones:          # backfill por si vienen de
    if "hid" not in _h:                           # una sesión/versión vieja
        _h["hid"] = st.session_state.hid_counter
        st.session_state.hid_counter += 1

def _next_hid():
    hid = st.session_state.hid_counter
    st.session_state.hid_counter += 1
    return hid

def _nombre_libre(prefijo):
    """Primer nombre 'prefijo N' que no esté usado (evita nombres repetidos)."""
    usados = {h["nombre"] for h in st.session_state.habitaciones}
    n = 1
    while f"{prefijo} {n}" in usados:
        n += 1
    return f"{prefijo} {n}"

def agregar():
    hid = _next_hid()
    st.session_state.habitaciones.append(
        {"hid": hid, "nombre": _nombre_libre("Habitación"), "tipo": "rect",
         "largo": 0.1, "ancho": 0.1, "altura": 0.30,
         "fijo": False, "forzar_h": False})

def agregar_l():
    hid = _next_hid()
    st.session_state.habitaciones.append(
        {"hid": hid, "nombre": _nombre_libre("Ambiente L"), "tipo": "l",
         "largo_total": 4.0, "ancho_total": 1.5,
         "largo_reducido": 1.5, "ancho_reducido": 0.5,
         "altura": 0.30, "forzar_h": False, "esquina": "inf_izq",
         "sentido": "auto"})

def eliminar(hid):
    st.session_state.habitaciones = [
        h for h in st.session_state.habitaciones if h.get("hid") != hid
    ]

for i, hab in enumerate(st.session_state.habitaciones):
    color    = HAB_COLORS[i%len(HAB_COLORS)]
    es_l     = hab.get("tipo") == "l"
    h_altura = "140px" if es_l else "100px"
    hid      = hab["hid"]
    col_color, col_form = st.columns([0.015, 0.985])
    with col_color:
        st.markdown(
            f'<div style="background:{color};width:6px;height:{h_altura};'
            f'border-radius:4px;margin-top:4px"></div>',
            unsafe_allow_html=True)
    with col_form:
        with st.container(border=True):
            if es_l:
                r1c1, r1c2, r1c3, r1c4, r1c5, r1c6, r1c7 = st.columns([2.2,1.1,1.1,1.1,1.1,1.2,0.5])
                with r1c1:
                    hab["nombre"] = st.text_input("Nombre", value=hab["nombre"],
                                                   key=f"nom_{hid}", label_visibility="collapsed")
                with r1c2:
                    hab["largo_total"]    = st.number_input("Largo total",    value=hab["largo_total"],
                                                             step=0.1, min_value=0.1, key=f"lt_{hid}")
                with r1c3:
                    hab["ancho_total"]    = st.number_input("Ancho total",    value=hab["ancho_total"],
                                                             step=0.1, min_value=0.1, key=f"at_{hid}")
                with r1c4:
                    hab["largo_reducido"] = st.number_input("Largo recorte", value=hab["largo_reducido"],
                                                             step=0.1, min_value=0.1, key=f"lr_{hid}")
                with r1c5:
                    hab["ancho_reducido"] = st.number_input("Ancho recorte", value=hab["ancho_reducido"],
                                                             step=0.1, min_value=0.1, key=f"ar_{hid}")
                with r1c6:
                    hab["altura"]  = st.number_input("Alt. susp.", value=hab.get("altura",0.30),
                                                      step=0.05, min_value=0.05, key=f"alt_{hid}")
                with r1c7:
                    st.write("")
                    if st.button("🗑️", key=f"del_{hid}",
                                 disabled=len(st.session_state.habitaciones)<=1):
                        eliminar(hid)
                        st.rerun()
                col_esq, col_svg = st.columns([1, 2])
                with col_esq:
                    hab["esquina"] = st.radio(
                        "Esquina del recorte",
                        options=["inf_izq", "inf_der", "sup_izq", "sup_der"],
                        format_func=lambda x: {
                            "inf_izq": "↙ Abajo izquierda",
                            "inf_der": "↘ Abajo derecha",
                            "sup_izq": "↖ Arriba izquierda",
                            "sup_der": "↗ Arriba derecha",
                        }[x],
                        index=["inf_izq","inf_der","sup_izq","sup_der"].index(
                            hab.get("esquina","inf_izq")),
                        key=f"esq_{hid}",
                    )
                    _opts_sent = ["auto", "largo", "ancho"]
                    hab["sentido"] = st.selectbox(
                        "Sentido de placas",
                        options=_opts_sent,
                        format_func=lambda x: {
                            "auto":  "🔄 Automático (óptimo)",
                            "largo": "→ Fijo a lo largo",
                            "ancho": "↓ Fijo a lo ancho",
                        }[x],
                        index=_opts_sent.index(hab.get("sentido", "auto")),
                        key=f"sent_{hid}",
                    )
                with col_svg:
                    try:
                        svg_l = diagrama_l_svg(
                            hab["largo_total"], hab["ancho_total"],
                            hab["largo_reducido"], hab["ancho_reducido"],
                            color, hab.get("esquina","inf_izq"))
                        st.markdown(svg_l, unsafe_allow_html=True)
                    except (ZeroDivisionError, ValueError, KeyError) as e:
                        # Pasa mientras el usuario está tipeando una medida a
                        # medio completar (ej. borró el 0 de "0.1"); no vale
                        # la pena interrumpir con un error rojo por eso.
                        st.caption(f"⏳ Dibujo no disponible con estos valores ({e}).")
            else:
                c1,c2,c3,c4,c5,c6,c7 = st.columns([2.2,1.2,1.2,1.2,1.6,1.4,0.5])
                with c1:
                    hab["nombre"] = st.text_input("Nombre", value=hab["nombre"],
                                                   key=f"nom_{hid}", label_visibility="collapsed")
                with c2:
                    hab["largo"]  = st.number_input("Largo (m)", value=hab["largo"],
                                                     step=0.1, min_value=0.1, key=f"lar_{hid}")
                with c3:
                    hab["ancho"]  = st.number_input("Ancho (m)", value=hab["ancho"],
                                                     step=0.1, min_value=0.1, key=f"anc_{hid}")
                with c4:
                    hab["altura"] = st.number_input("Alt. susp.", value=hab.get("altura",0.30),
                                                     step=0.05, min_value=0.05, key=f"alt_{hid}")
                with c5:
                    hab["fijo"]     = st.checkbox("Sentido fijo (→)", value=hab["fijo"], key=f"fij_{hid}")
                with c6:
                    hab["forzar_h"] = st.checkbox("🔗 Usar H", value=hab.get("forzar_h",False),
                                                   key=f"fh_{hid}",
                                                   help="Activa perfil H. Si la placa no alcanza, es obligatorio. Si alcanza, el motor busca el corte óptimo que ahorre más placas.")
                with c7:
                    st.write("")
                    if st.button("🗑️", key=f"del_{hid}",
                                 disabled=len(st.session_state.habitaciones)<=1):
                        eliminar(hid)
                        st.rerun()

col_btn1, col_btn2 = st.columns([1, 1])
with col_btn1:
    st.button("➕ Agregar habitación", on_click=agregar)
with col_btn2:
    st.button("📐 Agregar habitación en L", on_click=agregar_l)

# ═══════════════════════════════════════════════════════════════════════════
# CÁLCULO
# ═══════════════════════════════════════════════════════════════════════════

st.divider()

if not lens:
    st.error("Seleccioná al menos un largo de placa en la barra lateral.")
    st.stop()

habs = st.session_state.habitaciones

# Validación de ambientes en L
_l_invalidas = [h["nombre"] for h in habs if h.get("tipo") == "l" and (
    h["largo_reducido"] >= h["largo_total"] - EPS or
    h["ancho_reducido"] >= h["ancho_total"] - EPS)]
if _l_invalidas:
    st.error("El recorte tiene que ser más chico que el total en: "
             + ", ".join(_l_invalidas))
    st.stop()

# Paso 1: orientación y piezas de cada habitación
hab_info = []
for i, h in enumerate(habs):
    es_l = h.get("tipo") == "l"

    if es_l:
        lt, at = h["largo_total"], h["ancho_total"]
        otras_piezas_ctx = [p for h2 in hab_info for p in h2["piezas"]]
        sel = calcular_piezas_l(
            lt, at, h["largo_reducido"], h["ancho_reducido"], i, lens,
            otras_piezas=otras_piezas_ctx, usar_h=usar_h, precios=precios,
            forzar=h.get("sentido", "auto"))
        (dim_larga, filas_l), (dim_corta, filas_c) = sel["grupos"]
        info_larga = next((g for g in sel["info"] if g["dim"] == dim_larga), None)
        hab_info.append({
            **h, "idx": i, "orient": sel["orient"],
            "dim_pieza": dim_larga, "dim_pieza_orig": dim_larga,
            "filas": filas_l + filas_c, "filas_l": filas_l, "filas_c": filas_c,
            "dim_larga": dim_larga, "dim_corta": dim_corta,
            "piezas": sel["piezas"],
            "segmentos": info_larga["segs"] if info_larga else [dim_larga],
            "grupos_h": sel["info"],
            "n_h": sel["n_h"], "varillas_h": sel["varillas_h"],
            "auto_h": sel["n_h"] > 0, "h_opcional_aplicado": False,
            "area_real": hab_area(h),
            "largo": lt, "ancho": at,
            "transv": at if sel["orient"] == "largo" else lt,
            "fijo": h.get("sentido", "auto") in ("largo", "ancho"),
            "forzar_h": False,
        })
    else:
        orient, dim_pieza, filas, piezas, segmentos, n_h = orientacion_optima(h, i, lens)

        if not usar_h:
            n_h       = 0
            segmentos = [dim_pieza]
            piezas    = [{"dim": dim_pieza, "hab_idx": i}] * filas

        transv = h["ancho"] if orient == "largo" else h["largo"]
        hab_info.append({
            **h, "idx": i, "orient": orient,
            "dim_pieza": dim_pieza, "dim_pieza_orig": dim_pieza,
            "filas": filas, "piezas": piezas,
            "segmentos": segmentos, "n_h": n_h,
            "varillas_h": varillas_h(n_h, transv),
            "transv": transv,
            "auto_h": necesita_h(dim_pieza, lens) and usar_h,
            "h_opcional_aplicado": False,
            "area_real": hab_area(h),
        })

# Control: con H desactivado, no puede haber piezas más largas que la placa
_max_L  = max(lens)
_largas = sorted({(hi["nombre"], p["dim"]) for hi in hab_info for p in hi["piezas"]
                  if p["dim"] > _max_L + EPS})
if _largas:
    st.error(
        f"Hay tiras más largas que la placa máxima disponible ({_max_L} m): "
        + ", ".join(f"{n} ({d} m)" for n, d in _largas)
        + ". Activá **Usar perfil H** o habilitá placas más largas.")
    st.stop()

# Paso 2: H opcional en habitaciones rectangulares
if usar_h:
    candidatas = [h for h in hab_info if h.get("forzar_h", False) and h["n_h"] == 0]
    candidatas.sort(key=lambda h: h["dim_pieza"], reverse=True)

    for hc in candidatas:
        otras_piezas = [p for h in hab_info if h["idx"] != hc["idx"] for p in h["piezas"]]
        resultado = mejor_corte_h_opcional(
            hc["dim_pieza"], hc["filas"], hc["idx"], otras_piezas, lens)
        if resultado:
            seg1, seg2, _ = resultado
            nuevas_piezas = []
            for _ in range(hc["filas"]):
                nuevas_piezas.append({"dim": seg1, "hab_idx": hc["idx"]})
                nuevas_piezas.append({"dim": seg2, "hab_idx": hc["idx"]})
            hc["piezas"]              = nuevas_piezas
            hc["segmentos"]           = [seg1, seg2]
            hc["n_h"]                 = 1                                # una línea de unión
            hc["varillas_h"]          = varillas_h(1, hc["transv"])
            hc["h_opcional_aplicado"] = True

# Paso 3: plan de corte final (exacto + heurísticas, se queda con el más barato)
plan_opt     = mejorar_orientaciones(hab_info, lens, PW, precios)
todas_piezas = [p for h in hab_info for p in h["piezas"]]

candidatos_plan = [plan_opt, resolver_mixto(todas_piezas, lens)] + \
                  [resolver_con_largo(todas_piezas, L) for L in lens]
candidatos_plan = [c for c in candidatos_plan if c]
plan = min(candidatos_plan, key=lambda c: clave_plan(c, precios)) if candidatos_plan else []

# Control de consistencia: el plan tiene que cubrir TODAS las piezas
_demanda = Counter(_mm(p["dim"]) for p in todas_piezas)
_colocado = Counter(_mm(c["dim"]) for b in plan for c in b["cortes"])
_faltan = _demanda - _colocado
if _faltan:
    st.error("El plan de corte no cubre todas las piezas. Faltan: "
             + ", ".join(f"{n} × {d/_MM:.2f} m" for d, n in sorted(_faltan.items())))
    st.stop()

conteo = {4:0, 5:0, 6:0}
for b in plan:
    conteo[b["largo_placa"]] += 1
total_placas = len(plan)
total_area   = sum(hab_area(h) for h in habs)

# Desperdicio = sobrante a lo LARGO de las placas (lo que queda sin cortar).
# La última fila de cada ambiente, que se usa solo en parte del ancho
# (ej. 10 cm de los 20 cm), cuenta como placa aprovechada, NO como desperdicio.
metros_comp       = sum(b["largo_placa"] for b in plan)
metros_us         = sum(p["dim"] for p in todas_piezas)
m2_comprados      = metros_comp * PW
m2_aprovechados   = metros_us   * PW
m2_desperdiciados = max(0.0, (metros_comp - metros_us) * PW)
m2_borde          = max(0.0, m2_aprovechados - total_area)   # informativo
pct_desp = (m2_desperdiciados / m2_comprados * 100) if m2_comprados > 0 else 0

# Métrica estricta (solo para auditoría, NO es la que se muestra como
# principal): si se contara TAMBIÉN el borde de la última fila como material
# perdido, el desperdicio real de obra sería este. Criterio de negocio
# vigente: el borde NO cuenta como desperdicio (decisión explícita, no bug).
m2_desperdicio_estricto = max(0.0, m2_comprados - total_area)
pct_desp_estricto = (m2_desperdicio_estricto / m2_comprados * 100) if m2_comprados > 0 else 0

tot_h_perfiles = sum(h["n_h"] for h in hab_info)          # líneas de unión
tot_h_varillas = sum(h["varillas_h"] for h in hab_info)    # varillas de 4 m

estructuras = {}
for _h in hab_info:
    _perim = hab_perim(_h) if _h.get("tipo") == "l" else None
    estructuras[_h["idx"]] = calcular_estructura(
        hab_largo(_h), hab_ancho(_h), _h.get("altura", 0.30), _h["orient"],
        area_real=_h.get("area_real"), perim_real=_perim
    )

tot_sol  = sum(e["total_soleras"]    for e in estructuras.values())
tot_mon  = sum(e["total_montantes"]  for e in estructuras.values())
tot_mol  = sum(e["total_molduras"]   for e in estructuras.values())
tot_tar  = sum(e["total_tarugos_n8"] for e in estructuras.values())
tot_t1   = sum(e["total_t1"]         for e in estructuras.values())
tot_t2   = sum(e["total_t2"]         for e in estructuras.values())
total_perim = sum(e["perimetro"] for e in estructuras.values())

costo_placas = sum(conteo[l]*precios[l] for l in [4,5,6])
costo_h      = tot_h_varillas * p_h
costo_total  = (costo_placas + costo_h + tot_mol*pperim + tot_sol*psol +
                tot_mon*pmont + tot_tar*ptarug + tot_t1*ptorn_t1 + tot_t2*ptorn_t2)

# ═══════════════════════════════════════════════════════════════════════════
# MÉTRICAS
# ═══════════════════════════════════════════════════════════════════════════

for _aviso in AVISOS:
    st.warning(f"⚠️ {_aviso}")

st.subheader("📊 Resumen")

c1,c2,c3,c4,c5,c6 = st.columns(6)
c1.metric("Área total",        f"{total_area:.2f} m²")
c2.metric("Placas PVC",        f"{total_placas} un.")
c3.metric("Soleras (2.6m)",    f"{tot_sol} un.")
c4.metric("Montantes (2.6m)",  f"{tot_mon} un.")
c5.metric("Molduras PVC (4m)", f"{tot_mol} un.")
c6.metric("Tarugos N°8",       f"{tot_tar} un.")

st.write("")
ca,cb,cc,cd,ce,cf = st.columns(6)
ca.metric("✅ m² aprovechados",     f"{m2_aprovechados:.2f} m²",
          help=f"Incluye {m2_borde:.2f} m² de borde (última fila usada solo en "
               f"parte del ancho), que no se cuenta como desperdicio.")
cb.metric("❌ m² desperdicio",      f"{m2_desperdiciados:.2f} m²",
          delta=f"-{pct_desp:.1f}%", delta_color="inverse",
          help=f"Criterio vigente: no cuenta el borde de la última fila. Si se "
               f"contara TAMBIÉN ese borde como material perdido (criterio "
               f"estricto de auditoría), el desperdicio sería "
               f"{m2_desperdicio_estricto:.2f} m² ({pct_desp_estricto:.1f}%).")
cc.metric("📦 m² comprados",        f"{m2_comprados:.2f} m²")
cd.metric("🔩 Tornillos T1",        f"{tot_t1} un.")
ce.metric("🔧 Tornillos T2",        f"{tot_t2} un.")
cf.metric("🔗 Perfiles H (4m)",
          f"{tot_h_varillas} un." if tot_h_varillas>0 else "No necesario",
          help=f"{tot_h_perfiles} líneas de unión" if tot_h_perfiles>0 else "")

if costo_total > 0:
    st.metric("💰 Costo estimado total", f"${costo_total:,.0f}")

desc_mix = " | ".join(f"{conteo[l]} × {l}m" for l in [4,5,6] if conteo[l]>0)
info_h = f"  |  🔗 {tot_h_varillas} varillas H ({tot_h_perfiles} uniones)" if tot_h_varillas>0 else ""
st.info(f"**Mezcla óptima:** {desc_mix}{info_h}")

# ═══════════════════════════════════════════════════════════════════════════
# ESTRUCTURA POR HABITACIÓN
# ═══════════════════════════════════════════════════════════════════════════

st.subheader("🔧 Estructura por habitación")

filas_est = []
for h in hab_info:
    e = estructuras[h["idx"]]
    h_txt = f"{h['varillas_h']} var. ({h['n_h']} uniones)" if h["n_h"]>0 else "—"
    filas_est.append({
        "Habitación":   h["nombre"],
        "Dimensiones":  f"{hab_largo(h)}×{hab_ancho(h)}m",
        "Alt. susp.":   f"{h.get('altura',0.30):.2f}m",
        "Soleras":      e["total_soleras"],
        "Montantes":    e["total_montantes"],
        "Molduras PVC": e["total_molduras"],
        "Tarugos N°8":  e["total_tarugos_n8"],
        "Perfil H":     h_txt,
        "Torn. T1":     e["total_t1"],
        "Torn. T2":     e["total_t2"],
    })
st.dataframe(pd.DataFrame(filas_est), use_container_width=True, hide_index=True)

# ═══════════════════════════════════════════════════════════════════════════
# PLAN DE CORTE
# ═══════════════════════════════════════════════════════════════════════════

st.subheader("✂️ Plan de corte de placas")

leyenda = "".join(
    f'<span style="display:inline-flex;align-items:center;gap:5px;margin-right:14px;font-size:13px">'
    f'<span style="width:12px;height:12px;border-radius:3px;background:{HAB_COLORS[i%len(HAB_COLORS)]};display:inline-block"></span>'
    f'{h["nombre"]}{"  🔗H" if h["n_h"]>0 else ""}</span>'
    for i,h in enumerate(hab_info)
)
st.markdown(leyenda, unsafe_allow_html=True)

for h in hab_info:
    if h["n_h"] > 0:
        if h["auto_h"]:
            origen, icono = "obligatorio — la tira supera el largo máximo de placa", "🔴"
        elif h.get("h_opcional_aplicado"):
            origen, icono = "opcional aplicado — el motor encontró un corte que ahorra placas", "🟢"
        else:
            origen, icono = "opcional", "🔗"
        st.info(
            f"{icono} **{h['nombre']}** — H {origen} | "
            f"corte por fila: {texto_cortes_h(h)} | "
            f"{h['n_h']} línea(s) de unión | "
            f"{h['varillas_h']} varillas H de 4m"
        )
    elif h.get("forzar_h") and usar_h and not h["auto_h"]:
        st.caption(
            f"ℹ️ **{h['nombre']}**: H activado pero ningún corte mejora el resultado — "
            f"se mantiene sin H."
        )

st.write("")
LARGO_LABEL = {4:"🔵 Placas 4 m", 5:"🟢 Placas 5 m", 6:"🟠 Placas 6 m"}

for L in [4,5,6]:
    bins_L = [b for b in plan if b["largo_placa"]==L]
    if not bins_L:
        continue
    with st.expander(f"{LARGO_LABEL[L]} — {len(bins_L)} unidad{'es' if len(bins_L)>1 else ''}"):
        for idx, bin_ in enumerate(bins_L, 1):
            libre  = bin_["libre"]
            x_acum = 0.0
            segs   = ""
            for c in bin_["cortes"]:
                pct   = c["dim"]/L*100
                color = HAB_COLORS[c["hab_idx"]%len(HAB_COLORS)]
                label = f"{c['dim']}m" if c["dim"]>=0.3 else ""
                segs += (
                    f'<rect x="{x_acum:.3f}%" width="{pct:.3f}%" height="100%" fill="{color}"/>'
                    f'<text x="{x_acum+pct/2:.3f}%" y="55%" dominant-baseline="middle" '
                    f'text-anchor="middle" fill="white" font-size="11" font-weight="600">{label}</text>'
                )
                x_acum += pct
            sob = ""
            if libre > 0.001:
                lp = libre/L*100
                sob = f'<rect x="{x_acum:.3f}%" width="{lp:.3f}%" height="100%" fill="#e5e7eb"/>'
                if libre >= 0.15:
                    sob += (f'<text x="{x_acum+lp/2:.3f}%" y="55%" dominant-baseline="middle" '
                            f'text-anchor="middle" fill="#9ca3af" font-size="10">{libre:.2f}m</text>')
            svg = (f'<svg width="100%" height="22" xmlns="http://www.w3.org/2000/svg" '
                   f'style="border-radius:5px;overflow:hidden;border:0.5px solid #d1d5db">'
                   f'{segs}{sob}</svg>')
            libre_str = "sin sobrante" if libre<0.001 else f"{libre:.3f} m libre"
            cn2,cb2,ci = st.columns([0.5,8,1.5])
            with cn2:
                st.markdown(f'<span style="font-size:11px;color:#9ca3af">#{idx}</span>',
                            unsafe_allow_html=True)
            with cb2:
                st.markdown(svg, unsafe_allow_html=True)
            with ci:
                st.markdown(f'<span style="font-size:11px;color:#9ca3af">{libre_str}</span>',
                            unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════
# DETALLE PLACAS POR HABITACIÓN
# ═══════════════════════════════════════════════════════════════════════════

st.subheader("🏠 Detalle de placas por habitación")

filas_plac = []
for h in hab_info:
    es_l  = h.get("tipo") == "l"
    area  = h.get("area_real", hab_area(h))
    perim = hab_perim(h)
    if es_l:
        segs_txt = (f"{h['dim_larga']}m ({h['filas_l']} filas) + "
                    f"{h['dim_corta']}m ({h['filas_c']} filas)")
        if h["n_h"] > 0:
            segs_txt += f"  ·  con H: {texto_cortes_h(h)}"
        dir_txt  = ("→ largo total" if h["orient"]=="largo" else "↓ ancho total") \
                   + (" (fijo)" if h.get("fijo") else "")
        tipo_txt = "📐 L"
    else:
        segs_txt = texto_cortes_h(h) if h["n_h"]>0 else f"{h['dim_pieza']}m"
        dir_txt  = ("→ largo" if h["orient"]=="largo" else "↓ ancho")+(" (fijo)" if h.get("fijo") else "")
        tipo_txt = "▭"
    filas_plac.append({
        "Tipo":           tipo_txt,
        "Habitación":     h["nombre"],
        "Área (m²)":      round(area, 2),
        "Dirección":      dir_txt,
        "Corte por fila": segs_txt,
        "Filas totales":  h["filas"],
        "Perím. (m)":     round(perim, 1),
        "Perfil H":       f"{h['n_h']} uniones / {h['varillas_h']} var." if h["n_h"]>0 else "—",
    })
st.dataframe(pd.DataFrame(filas_plac), use_container_width=True, hide_index=True)

# ═══════════════════════════════════════════════════════════════════════════
# PDF
# ═══════════════════════════════════════════════════════════════════════════

st.divider()
st.subheader("📄 Generar reporte PDF")

descripcion = st.text_input("Obra / Descripción (opcional)",
                             placeholder="Ej: Casa González — Tucumán 123")

if st.button("📥 Generar PDF", type="primary"):
    with st.spinner("Generando PDF..."):
        pdf_bytes = generar_pdf(
            habs, hab_info, plan, todas_piezas,
            total_area, tot_sol, tot_mon, tot_mol, tot_tar,
            tot_t1, tot_t2, tot_h_perfiles, tot_h_varillas,
            m2_comprados, m2_desperdiciados, pct_desp,
            conteo, costo_total, estructuras, descripcion,
        )
    st.download_button(
        label="⬇️ Descargar PDF",
        data=pdf_bytes,
        file_name=f"plan_corte_{date.today().strftime('%Y%m%d')}.pdf",
        mime="application/pdf",
    )

st.caption("Motor: optimización exacta global (ILP por patrones) con respaldo heurístico. "
           "Perfil H: tiras largas partidas en tramos de placa máxima; también en ambientes en L. "
           "Ancho placa: 20 cm fijo.")
