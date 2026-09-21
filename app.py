import streamlit as st
import math
import pandas as pd

st.set_page_config(
    page_title="Presupuestador Cielorrasos PVC",
    page_icon="🏠",
    layout="wide",
)

# Paleta de colores por habitación
HAB_COLORS = [
    "#2563EB","#16A34A","#DC2626","#9333EA","#D97706",
    "#0891B2","#DB2777","#65A30D","#EA580C","#7C3AED"
]

st.title("🏠 Presupuestador Cielorrasos PVC")
st.caption("Optimización global de corte — mezcla de largos, colores por habitación, reutilización de sobrantes entre habitaciones")

# ─── MOTOR DE CORTE ───────────────────────────────────────────────────────────

def mejor_largo(d, lens, kerf):
    best_L, best_desp = None, float("inf")
    for L in lens:
        if L < d - 0.001:
            continue
        n = math.floor((L + kerf) / (d + kerf))
        if n == 0:
            continue
        desp = (L - n * d - (n - 1) * kerf) / L
        if desp < best_desp:
            best_desp = desp
            best_L = L
    return best_L


def resolver_corte(piezas, lens, kerf):
    """
    piezas: lista de {"dim": float, "hab_idx": int}
    Devuelve plan: lista de {"largo_placa": float, "cortes": [...], "libre": float}
    """
    sorted_p = sorted(piezas, key=lambda x: -x["dim"])
    sobrantes = []
    plan = []

    for p in sorted_p:
        sobrantes.sort(key=lambda s: s["libre"])
        usado = False
        for s in sobrantes:
            if s["libre"] >= p["dim"] - 0.0001:
                bin_ = plan[s["plan_idx"]]
                bin_["cortes"].append({"dim": p["dim"], "hab_idx": p["hab_idx"]})
                s["libre"] = max(0, s["libre"] - p["dim"] - kerf)
                bin_["libre"] = s["libre"]
                usado = True
                break
        if not usado:
            L = mejor_largo(p["dim"], lens, kerf)
            if L is None:
                continue
            libre = max(0, L - p["dim"] - kerf)
            idx = len(plan)
            plan.append({"largo_placa": L, "cortes": [{"dim": p["dim"], "hab_idx": p["hab_idx"]}], "libre": libre})
            sobrantes.append({"plan_idx": idx, "libre": libre})

    return plan


def orientacion_optima(r, idx, lens, kerf, pw, fijo):
    filas_largo = math.ceil(r["ancho"] / pw)
    filas_ancho  = math.ceil(r["largo"] / pw)
    piezas_largo = [{"dim": r["largo"], "hab_idx": idx}] * filas_largo
    piezas_ancho  = [{"dim": r["ancho"],  "hab_idx": idx}] * filas_ancho

    if fijo:
        return "largo", r["largo"], filas_largo, piezas_largo

    plan_largo = resolver_corte(piezas_largo, lens, kerf)
    plan_ancho  = resolver_corte(piezas_ancho,  lens, kerf)

    if len(plan_largo) <= len(plan_ancho):
        return "largo", r["largo"], filas_largo, piezas_largo
    else:
        return "ancho",  r["ancho"],  filas_ancho,  piezas_ancho

# ─── SIDEBAR ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("⚙️ Configuración")

    st.subheader("Placas disponibles")
    usar_4 = st.checkbox("Placa 4 m", value=True)
    usar_5 = st.checkbox("Placa 5 m", value=True)
    usar_6 = st.checkbox("Placa 6 m", value=True)
    lens = [l for l, u in [(4, usar_4), (5, usar_5), (6, usar_6)] if u]

    st.subheader("Medidas")
    pw   = st.number_input("Ancho de placa (m)",      value=0.25,  step=0.01,  min_value=0.05)
    kerf = st.number_input("Pérdida sierra (m)",       value=0.003, step=0.001, format="%.3f")
    ms   = st.number_input("Separación montantes (m)", value=0.60,  step=0.05)
    sm2  = st.number_input("Tornillos por m²",         value=12,    step=1,     min_value=1)

    st.subheader("Precios (opcional)")
    p4     = st.number_input("Placa 4 m ($ / un)",  value=0.0, step=100.0, format="%.0f")
    p5     = st.number_input("Placa 5 m ($ / un)",  value=0.0, step=100.0, format="%.0f")
    p6     = st.number_input("Placa 6 m ($ / un)",  value=0.0, step=100.0, format="%.0f")
    pperim = st.number_input("Perimetral ($ / m)",   value=0.0, step=100.0, format="%.0f")
    pmont  = st.number_input("Montante ($ / m)",     value=0.0, step=100.0, format="%.0f")
    ptorn  = st.number_input("Tornillo ($ / un)",    value=0.0, step=10.0,  format="%.0f")
    precios = {4: p4, 5: p5, 6: p6}

# ─── HABITACIONES ─────────────────────────────────────────────────────────────

st.subheader("📐 Habitaciones")

if "habitaciones" not in st.session_state:
    st.session_state.habitaciones = [
        {"nombre": "Habitación 1", "largo": 3.5, "ancho": 4.0, "fijo": False},
        {"nombre": "Habitación 2", "largo": 2.5, "ancho": 3.0, "fijo": False},
    ]

def agregar():
    n = len(st.session_state.habitaciones) + 1
    st.session_state.habitaciones.append({"nombre": f"Habitación {n}", "largo": 0.0, "ancho": 0.0, "fijo": False})

def eliminar(i):
    st.session_state.habitaciones.pop(i)

for i, hab in enumerate(st.session_state.habitaciones):
    color = HAB_COLORS[i % len(HAB_COLORS)]
    # franja de color a la izquierda usando columna angosta
    col_color, col_form = st.columns([0.015, 0.985])
    with col_color:
        st.markdown(
            f'<div style="background:{color};width:6px;height:80px;border-radius:4px;margin-top:4px"></div>',
            unsafe_allow_html=True,
        )
    with col_form:
        with st.container(border=True):
            c1, c2, c3, c4, c5 = st.columns([3, 1.5, 1.5, 2, 0.5])
            with c1:
                hab["nombre"] = st.text_input("Nombre", value=hab["nombre"], key=f"nom_{i}",
                                               label_visibility="collapsed")
            with c2:
                hab["largo"] = st.number_input("Largo (m)", value=hab["largo"], step=0.1,
                                                min_value=0.1, key=f"lar_{i}")
            with c3:
                hab["ancho"] = st.number_input("Ancho (m)", value=hab["ancho"], step=0.1,
                                                min_value=0.1, key=f"anc_{i}")
            with c4:
                hab["fijo"] = st.checkbox("Sentido fijo (→ largo)", value=hab["fijo"], key=f"fij_{i}")
            with c5:
                st.write("")
                if st.button("🗑️", key=f"del_{i}", disabled=len(st.session_state.habitaciones) <= 1):
                    eliminar(i)
                    st.rerun()

st.button("➕ Agregar habitación", on_click=agregar)

# ─── VALIDACIÓN ───────────────────────────────────────────────────────────────

st.divider()

if not lens:
    st.error("Seleccioná al menos un largo de placa en la barra lateral.")
    st.stop()

habs = st.session_state.habitaciones

# ─── CÁLCULO ──────────────────────────────────────────────────────────────────

hab_info = []
for i, h in enumerate(habs):
    orient, dim_pieza, filas, piezas = orientacion_optima(h, i, lens, kerf, pw, h["fijo"])
    hab_info.append({**h, "idx": i, "orient": orient, "dim_pieza": dim_pieza,
                     "filas": filas, "piezas": piezas})

todas_piezas = [p for h in hab_info for p in h["piezas"]]
plan = resolver_corte(todas_piezas, lens, kerf)

conteo = {4: 0, 5: 0, 6: 0}
for b in plan:
    conteo[b["largo_placa"]] += 1
total_placas = len(plan)

total_area  = sum(h["largo"] * h["ancho"] for h in habs)
total_perim = sum(2 * (h["largo"] + h["ancho"]) for h in habs)
total_mont, total_screw = 0, 0

for h in hab_info:
    dim_par  = h["ancho"] if h["orient"] == "largo" else h["largo"]
    dim_perp = h["largo"] if h["orient"] == "largo" else h["ancho"]
    total_mont  += (math.ceil(dim_par / ms) + 1) * dim_perp
    total_screw += math.ceil(h["largo"] * h["ancho"] * sm2)

costo_placas = sum(conteo[l] * precios[l] for l in [4, 5, 6])
costo_total  = costo_placas + total_perim * pperim + total_mont * pmont + total_screw * ptorn

metros_usados    = sum(p["dim"] for p in todas_piezas)
metros_comprados = sum(b["largo_placa"] for b in plan)
pct_desp = (1 - metros_usados / metros_comprados) * 100 if metros_comprados > 0 else 0

# ─── MÉTRICAS ─────────────────────────────────────────────────────────────────

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

desc = " | ".join(f"{conteo[l]} × {l}m" for l in [4, 5, 6] if conteo[l] > 0)
st.info(f"**Mezcla óptima:** {desc}")

# ─── PLAN DE CORTE CON COLORES POR HABITACIÓN ─────────────────────────────────

st.subheader("✂️ Plan de corte")

# Leyenda
leyenda_html = "".join(
    f'<span style="display:inline-flex;align-items:center;gap:5px;margin-right:12px;font-size:13px">'
    f'<span style="width:12px;height:12px;border-radius:3px;background:{HAB_COLORS[i % len(HAB_COLORS)]};display:inline-block"></span>'
    f'{h["nombre"]}</span>'
    for i, h in enumerate(habs)
)
st.markdown(leyenda_html, unsafe_allow_html=True)
st.write("")

LARGO_LABEL = {4: "🔵 Placas 4 m", 5: "🟢 Placas 5 m", 6: "🟠 Placas 6 m"}

for L in [4, 5, 6]:
    bins_L = [b for b in plan if b["largo_placa"] == L]
    if not bins_L:
        continue

    with st.expander(f"{LARGO_LABEL[L]} — {len(bins_L)} unidad{'es' if len(bins_L) > 1 else ''}"):
        for idx, bin_ in enumerate(bins_L, 1):
            libre = bin_["libre"]

            # Barra SVG
            segs = ""
            for c in bin_["cortes"]:
                pct = c["dim"] / L * 100
                color = HAB_COLORS[c["hab_idx"] % len(HAB_COLORS)]
                nombre_hab = habs[c["hab_idx"]]["nombre"] if c["hab_idx"] < len(habs) else "?"
                label = f"{c['dim']}m" if c["dim"] >= 0.5 else ""
                segs += (
                    f'<title>{nombre_hab} — {c["dim"]}m</title>'
                    f'<rect x="{sum(cc["dim"]/L*100 for cc in bin_["cortes"][:bin_["cortes"].index(c)])}%" '
                    f'width="{pct}%" height="100%" fill="{color}"/>'
                    f'<text x="{sum(cc["dim"]/L*100 for cc in bin_["cortes"][:bin_["cortes"].index(c)]) + pct/2}%" '
                    f'y="55%" dominant-baseline="middle" text-anchor="middle" '
                    f'fill="white" font-size="11" font-weight="600">{label}</text>'
                )

            libre_pct = libre / L * 100
            libre_x   = 100 - libre_pct

            svg = f"""
            <svg width="100%" height="22" xmlns="http://www.w3.org/2000/svg" style="border-radius:5px;overflow:hidden;border:0.5px solid #ccc">
              {segs}
              {"" if libre < 0.01 else f'<rect x="{libre_x}%" width="{libre_pct}%" height="100%" fill="#e5e7eb"/>'
               + (f'<text x="{libre_x + libre_pct/2}%" y="55%" dominant-baseline="middle" text-anchor="middle" fill="#9ca3af" font-size="10">{libre:.2f}m</text>' if libre >= 0.3 else "")}
            </svg>
            """

            libre_str = "sin sobrante" if libre < 0.01 else f"{libre:.3f} m libre"
            col_num, col_bar, col_info = st.columns([0.5, 8, 1.5])
            with col_num:
                st.markdown(f'<span style="font-size:11px;color:#9ca3af">#{idx}</span>', unsafe_allow_html=True)
            with col_bar:
                st.markdown(svg, unsafe_allow_html=True)
            with col_info:
                st.markdown(f'<span style="font-size:11px;color:#9ca3af">{libre_str}</span>', unsafe_allow_html=True)

# ─── DETALLE POR HABITACIÓN ───────────────────────────────────────────────────

st.subheader("🏠 Detalle por habitación")

filas_tabla = []
for i, h in enumerate(hab_info):
    area  = h["largo"] * h["ancho"]
    perim = 2 * (h["largo"] + h["ancho"])
    dim_par  = h["ancho"] if h["orient"] == "largo" else h["largo"]
    dim_perp = h["largo"] if h["orient"] == "largo" else h["ancho"]
    mont = (math.ceil(dim_par / ms) + 1) * dim_perp
    filas_tabla.append({
        "Color":         f"●",   # se reemplaza abajo con HTML
        "Habitación":    h["nombre"],
        "Área (m²)":     round(area, 2),
        "Dirección":     f"{'→ largo' if h['orient'] == 'largo' else '↓ ancho'}{'  (fijo)' if h['fijo'] else ''}",
        "Pieza (m)":     h["dim_pieza"],
        "Filas":         h["filas"],
        "Perím. (m)":    round(perim, 1),
        "Montantes (m)": round(mont, 1),
        "Tornillos":     math.ceil(h["largo"] * h["ancho"] * sm2),
    })

df = pd.DataFrame(filas_tabla)
st.dataframe(df, use_container_width=True, hide_index=True)

st.caption("Motor: bin-packing 1D mixto con reutilización global de sobrantes. Cada color representa una habitación.")
