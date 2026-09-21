import streamlit as st
import math

st.set_page_config(
    page_title="Presupuestador Cielorrasos PVC",
    page_icon="🏠",
    layout="wide",
)

st.title("🏠 Presupuestador Cielorrasos PVC")
st.caption("Optimización global de corte — mezcla de largos, reutilización entre habitaciones")

# ─── MOTOR DE CORTE ───────────────────────────────────────────────────────────

def mejor_largo_para_pieza(d, lens, kerf):
    """Elige el largo de placa que minimiza el desperdicio para una pieza de longitud d."""
    best_L, best_desp = None, float("inf")
    for L in lens:
        if L < d - 0.001:
            continue
        piezas_por_placa = math.floor((L + kerf) / (d + kerf))
        if piezas_por_placa == 0:
            continue
        usado = piezas_por_placa * d + (piezas_por_placa - 1) * kerf
        sobra = L - usado
        desp = sobra / L
        if desp < best_desp:
            best_desp = desp
            best_L = L
    return best_L


def resolver_corte(piezas_all, lens, kerf):
    """
    Bin-packing 1D mixto: para cada pieza elige el largo óptimo o reutiliza
    un sobrante existente. Devuelve lista de bins con largoPlaca, cortes y libre.
    """
    sorted_piezas = sorted(piezas_all, reverse=True)
    sobrantes = []   # {"plan_idx": int, "libre": float}
    plan = []        # {"largo_placa": float, "cortes": [], "libre": float}

    for d in sorted_piezas:
        # Intentar usar sobrante existente (el más ajustado primero)
        sobrantes.sort(key=lambda s: s["libre"])
        usado = False
        for s in sobrantes:
            if s["libre"] >= d - 0.0001:
                bin_ = plan[s["plan_idx"]]
                bin_["cortes"].append(d)
                s["libre"] = max(0, s["libre"] - d - kerf)
                bin_["libre"] = s["libre"]
                usado = True
                break
        if not usado:
            L = mejor_largo_para_pieza(d, lens, kerf)
            if L is None:
                continue
            libre = max(0, L - d - kerf)
            idx = len(plan)
            plan.append({"largo_placa": L, "cortes": [d], "libre": libre})
            sobrantes.append({"plan_idx": idx, "libre": libre})

    return plan


def orientacion_optima(largo, ancho, lens, kerf, pw, fijo):
    """Devuelve la orientación (largo/ancho) y las piezas necesarias."""
    filas_largo = math.ceil(ancho / pw)
    filas_ancho  = math.ceil(largo / pw)

    piezas_largo = [largo] * filas_largo
    piezas_ancho = [ancho]  * filas_ancho

    if fijo:
        return "largo", largo, filas_largo, piezas_largo

    plan_largo = resolver_corte(piezas_largo, lens, kerf)
    plan_ancho  = resolver_corte(piezas_ancho,  lens, kerf)

    if len(plan_largo) <= len(plan_ancho):
        return "largo", largo, filas_largo, piezas_largo
    else:
        return "ancho", ancho, filas_ancho, piezas_ancho


# ─── SIDEBAR — CONFIGURACIÓN ──────────────────────────────────────────────────

with st.sidebar:
    st.header("⚙️ Configuración")

    st.subheader("Placas disponibles")
    usar_4 = st.checkbox("Placa 4 m", value=True)
    usar_5 = st.checkbox("Placa 5 m", value=True)
    usar_6 = st.checkbox("Placa 6 m", value=True)

    lens = []
    if usar_4: lens.append(4)
    if usar_5: lens.append(5)
    if usar_6: lens.append(6)

    st.subheader("Medidas")
    pw   = st.number_input("Ancho de placa (m)",     value=0.25, step=0.01, min_value=0.05)
    kerf = st.number_input("Pérdida sierra (m)",      value=0.003, step=0.001, format="%.3f")
    ms   = st.number_input("Separación montantes (m)", value=0.60, step=0.05)
    sm2  = st.number_input("Tornillos por m²",         value=12,   step=1,    min_value=1)

    st.subheader("Precios (opcional)")
    p4    = st.number_input("Placa 4 m ($ / un)",   value=0.0, step=100.0, format="%.0f")
    p5    = st.number_input("Placa 5 m ($ / un)",   value=0.0, step=100.0, format="%.0f")
    p6    = st.number_input("Placa 6 m ($ / un)",   value=0.0, step=100.0, format="%.0f")
    pperim = st.number_input("Perimetral ($ / m)",   value=0.0, step=100.0, format="%.0f")
    pmont  = st.number_input("Montante ($ / m)",     value=0.0, step=100.0, format="%.0f")
    ptorn  = st.number_input("Tornillo ($ / un)",    value=0.0, step=10.0,  format="%.0f")

# ─── HABITACIONES ─────────────────────────────────────────────────────────────

st.subheader("📐 Habitaciones")

if "habitaciones" not in st.session_state:
    st.session_state.habitaciones = [
        {"nombre": "Habitación 1", "largo": 3.5, "ancho": 4.0, "fijo": False},
        {"nombre": "Habitación 2", "largo": 2.5, "ancho": 3.0, "fijo": False},
    ]

def agregar():
    n = len(st.session_state.habitaciones) + 1
    st.session_state.habitaciones.append(
        {"nombre": f"Habitación {n}", "largo": 0.0, "ancho": 0.0, "fijo": False}
    )

def eliminar(i):
    st.session_state.habitaciones.pop(i)

for i, hab in enumerate(st.session_state.habitaciones):
    with st.container(border=True):
        col1, col2, col3, col4, col5 = st.columns([3, 1.5, 1.5, 2, 0.5])
        with col1:
            hab["nombre"] = st.text_input("Nombre", value=hab["nombre"], key=f"nom_{i}")
        with col2:
            hab["largo"]  = st.number_input("Largo (m)", value=hab["largo"], step=0.1, min_value=0.1, key=f"lar_{i}")
        with col3:
            hab["ancho"]  = st.number_input("Ancho (m)", value=hab["ancho"], step=0.1, min_value=0.1, key=f"anc_{i}")
        with col4:
            hab["fijo"]   = st.checkbox("Sentido fijo (largo)", value=hab["fijo"], key=f"fij_{i}")
        with col5:
            st.write("")
            st.write("")
            if st.button("🗑️", key=f"del_{i}", disabled=len(st.session_state.habitaciones) <= 1):
                eliminar(i)
                st.rerun()

st.button("➕ Agregar habitación", on_click=agregar)

# ─── CÁLCULO ──────────────────────────────────────────────────────────────────

st.divider()

if not lens:
    st.error("Seleccioná al menos un largo de placa en la barra lateral.")
    st.stop()

habs = st.session_state.habitaciones

# Orientación por habitación
hab_info = []
for h in habs:
    orient, dim_pieza, filas, piezas = orientacion_optima(
        h["largo"], h["ancho"], lens, kerf, pw, h["fijo"]
    )
    hab_info.append({**h, "orient": orient, "dim_pieza": dim_pieza,
                     "filas": filas, "piezas": piezas})

# Corte global mixto
todas_piezas = [p for h in hab_info for p in h["piezas"]]
plan = resolver_corte(todas_piezas, lens, kerf)

# Conteo por largo
conteo = {4: 0, 5: 0, 6: 0}
for b in plan:
    conteo[b["largo_placa"]] += 1
total_placas = len(plan)

# Otros materiales
total_area  = sum(h["largo"] * h["ancho"] for h in habs)
total_perim = sum(2 * (h["largo"] + h["ancho"]) for h in habs)
total_mont  = 0
total_screw = 0

for h in hab_info:
    dim_par  = h["ancho"] if h["orient"] == "largo" else h["largo"]
    dim_perp = h["largo"] if h["orient"] == "largo" else h["ancho"]
    total_mont  += (math.ceil(dim_par / ms) + 1) * dim_perp
    total_screw += math.ceil(h["largo"] * h["ancho"] * sm2)

# Costo
costo_placas = conteo[4]*p4 + conteo[5]*p5 + conteo[6]*p6
costo_total  = costo_placas + total_perim*pperim + total_mont*pmont + total_screw*ptorn

metros_usados    = sum(todas_piezas)
metros_comprados = sum(b["largo_placa"] for b in plan)
pct_desp = (1 - metros_usados / metros_comprados) * 100 if metros_comprados > 0 else 0

# ─── RESULTADOS — MÉTRICAS ────────────────────────────────────────────────────

st.subheader("📊 Resumen")

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Área total",       f"{total_area:.2f} m²")
c2.metric("Total placas",     f"{total_placas} un.")
c3.metric("Desperdicio real", f"{pct_desp:.1f} %")
c4.metric("Perimetral",       f"{total_perim:.1f} m")
c5.metric("Montantes",        f"{total_mont:.1f} m")
c6.metric("Tornillos",        f"{total_screw} un.")

if costo_total > 0:
    st.metric("💰 Costo estimado", f"${costo_total:,.0f}")

# Desglose de placas por largo
desc_placas = " | ".join(
    f"{conteo[l]} placas de {l}m" for l in [4, 5, 6] if conteo[l] > 0
)
st.info(f"**Mezcla óptima:** {desc_placas}")

# ─── DETALLE POR HABITACIÓN ───────────────────────────────────────────────────

st.subheader("🏠 Detalle por habitación")

import pandas as pd

filas_tabla = []
for h in hab_info:
    area  = h["largo"] * h["ancho"]
    perim = 2 * (h["largo"] + h["ancho"])
    dim_par  = h["ancho"] if h["orient"] == "largo" else h["largo"]
    dim_perp = h["largo"] if h["orient"] == "largo" else h["ancho"]
    mont = (math.ceil(dim_par / ms) + 1) * dim_perp
    filas_tabla.append({
        "Habitación":  h["nombre"],
        "Área (m²)":   round(area, 2),
        "Dirección":   f"{'→ largo' if h['orient']=='largo' else '↓ ancho'}{'  (fijo)' if h['fijo'] else ''}",
        "Long. pieza": f"{h['dim_pieza']} m",
        "Filas":       h["filas"],
        "Perím. (m)":  round(perim, 1),
        "Montantes (m)": round(mont, 1),
        "Tornillos":   math.ceil(area * sm2),
    })

st.dataframe(pd.DataFrame(filas_tabla), use_container_width=True, hide_index=True)

# ─── PLAN DE CORTE ────────────────────────────────────────────────────────────

st.subheader("✂️ Plan de corte mixto")

for L in [4, 5, 6]:
    bins_L = [b for b in plan if b["largo_placa"] == L]
    if not bins_L:
        continue
    with st.expander(f"Placas de {L} m — {len(bins_L)} unidad{'es' if len(bins_L)>1 else ''}"):
        filas_corte = []
        for idx, b in enumerate(bins_L, 1):
            cortes_str = " + ".join(f"{c}m" for c in b["cortes"])
            libre_str  = f"{b['libre']:.3f} m" if b["libre"] > 0.005 else "sin sobrante"
            filas_corte.append({
                "Placa": f"#{idx}",
                "Cortes": cortes_str,
                "Sobrante": libre_str,
                "Uso (%)": f"{(1 - b['libre']/L)*100:.1f}%",
            })
        st.dataframe(pd.DataFrame(filas_corte), use_container_width=True, hide_index=True)

st.caption("Motor de optimización: bin-packing mixto con reutilización global de sobrantes entre habitaciones.")
