import streamlit as st
import math
import pandas as pd
from datetime import date
import io
import base64

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, Image as RLImage,
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

st.set_page_config(
    page_title="Presupuestador Cielorrasos PVC",
    page_icon="🏠",
    layout="wide",
)

HAB_COLORS = [
    "#2563EB","#16A34A","#DC2626","#9333EA","#D97706",
    "#0891B2","#DB2777","#65A30D","#EA580C","#7C3AED"
]

NAVY = colors.HexColor("#1B2A6B")
LIGHT_GRAY = colors.HexColor("#F3F4F6")
MID_GRAY   = colors.HexColor("#D1D5DB")
DARK_GRAY  = colors.HexColor("#374151")

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
        usado = n * d + (n - 1) * kerf
        desp  = (L - usado) / L
        if desp < best_desp:
            best_desp = desp
            best_L    = L
    return best_L


def resolver_corte(piezas, lens, kerf):
    sorted_p  = sorted(piezas, key=lambda x: -x["dim"])
    sobrantes = []
    plan      = []
    for p in sorted_p:
        sobrantes.sort(key=lambda s: s["libre"])
        usado = False
        for s in sobrantes:
            if s["libre"] >= p["dim"] - 0.0001:
                bin_ = plan[s["plan_idx"]]
                bin_["cortes"].append({"dim": p["dim"], "hab_idx": p["hab_idx"]})
                s["libre"]   = max(0.0, s["libre"] - p["dim"] - kerf)
                bin_["libre"] = s["libre"]
                usado = True
                break
        if not usado:
            L = mejor_largo(p["dim"], lens, kerf)
            if L is None:
                continue
            libre = max(0.0, L - p["dim"] - kerf)
            idx   = len(plan)
            plan.append({"largo_placa": L,
                         "cortes": [{"dim": p["dim"], "hab_idx": p["hab_idx"]}],
                         "libre": libre})
            sobrantes.append({"plan_idx": idx, "libre": libre})
    return plan


def orientacion_optima(r, idx, lens, kerf, pw, fijo):
    filas_largo  = math.ceil(r["ancho"] / pw)
    filas_ancho  = math.ceil(r["largo"] / pw)
    piezas_largo = [{"dim": r["largo"], "hab_idx": idx}] * filas_largo
    piezas_ancho = [{"dim": r["ancho"],  "hab_idx": idx}] * filas_ancho
    if fijo:
        return "largo", r["largo"], filas_largo, piezas_largo
    plan_largo = resolver_corte(piezas_largo, lens, kerf)
    plan_ancho  = resolver_corte(piezas_ancho,  lens, kerf)
    if len(plan_largo) <= len(plan_ancho):
        return "largo", r["largo"], filas_largo, piezas_largo
    else:
        return "ancho",  r["ancho"],  filas_ancho,  piezas_ancho

# ─── GENERADOR DE PDF ─────────────────────────────────────────────────────────

def generar_pdf(habs, hab_info, plan, todas_piezas,
                total_area, total_perim, total_mont, total_screw,
                m2_comprados, m2_aprovechados, m2_desperdiciados, pct_desp,
                conteo, costo_total, pw, descripcion):

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
        topMargin=1.5*cm,  bottomMargin=2*cm,
    )

    styles = getSampleStyleSheet()
    s_title  = ParagraphStyle("title",  fontSize=15, textColor=NAVY,
                               fontName="Helvetica-Bold", spaceAfter=2)
    s_sub    = ParagraphStyle("sub",    fontSize=10, textColor=NAVY,
                               fontName="Helvetica-Bold", spaceBefore=10, spaceAfter=4)
    s_body   = ParagraphStyle("body",   fontSize=8,  textColor=DARK_GRAY,
                               fontName="Helvetica",  spaceAfter=3)
    s_small  = ParagraphStyle("small",  fontSize=7,  textColor=colors.HexColor("#6B7280"),
                               fontName="Helvetica")
    s_footer = ParagraphStyle("footer", fontSize=7.5, textColor=DARK_GRAY,
                               fontName="Helvetica-Oblique", alignment=TA_CENTER)
    s_center = ParagraphStyle("center", fontSize=8, textColor=DARK_GRAY,
                               fontName="Helvetica", alignment=TA_CENTER)

    story = []
    W = A4[0] - 3.6*cm   # ancho útil

    # ── ENCABEZADO ──────────────────────────────────────────────────────────
    try:
        logo = RLImage("/home/claude/cielorrasos/logo.png", width=4.5*cm, height=1.8*cm)
        logo.hAlign = "LEFT"
        fecha_str = date.today().strftime("%d/%m/%Y")
        header_data = [[logo, Paragraph(f"<b>Fecha:</b> {fecha_str}", s_body)]]
        header_table = Table(header_data, colWidths=[W * 0.6, W * 0.4])
        header_table.setStyle(TableStyle([
            ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN",       (1, 0), (1, 0),   "RIGHT"),
        ]))
        story.append(header_table)
    except Exception:
        story.append(Paragraph(f"<b>LAUTHARTE MATERIALES</b> — {date.today().strftime('%d/%m/%Y')}", s_title))

    story.append(HRFlowable(width="100%", thickness=1.5, color=NAVY, spaceAfter=6))

    if descripcion.strip():
        story.append(Paragraph(f"<b>Obra / Descripción:</b> {descripcion.strip()}", s_body))
        story.append(Spacer(1, 4))

    # ── RESUMEN DE MATERIALES ───────────────────────────────────────────────
    story.append(Paragraph("RESUMEN DE MATERIALES", s_sub))

    desc_mezcla = "  |  ".join(f"{conteo[l]} × {l}m" for l in [4,5,6] if conteo.get(l,0) > 0)
    resumen_data = [
        ["Área total", "Placas PVC", "Perimetral", "Montantes", "Tornillos"],
        [f"{total_area:.2f} m²", desc_mezcla,
         f"{total_perim:.1f} m", f"{total_mont:.1f} m", f"{total_screw} un."],
        ["m² aprovechados", "m² desperdicio", "m² comprados", "Desperdicio %", ""],
        [f"{m2_aprovechados:.2f} m²", f"{m2_desperdiciados:.2f} m²",
         f"{m2_comprados:.2f} m²", f"{pct_desp:.1f} %", ""],
    ]
    if costo_total > 0:
        resumen_data[0].append("Costo estimado")
        resumen_data[1].append(f"${costo_total:,.0f}")

    col_n  = len(resumen_data[0])
    col_w  = W / col_n
    t_res  = Table(resumen_data, colWidths=[col_w] * col_n, repeatRows=0)
    t_res.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
        ("BACKGROUND",  (0, 2), (-1, 2), NAVY),
        ("TEXTCOLOR",   (0, 2), (-1, 2), colors.white),
        ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME",    (0, 2), (-1, 2), "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, -1), 8),
        ("ALIGN",       (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, 1), [LIGHT_GRAY]),
        ("ROWBACKGROUNDS", (0, 3), (-1, 3), [LIGHT_GRAY]),
        ("GRID",        (0, 0), (-1, -1), 0.4, MID_GRAY),
        ("TOPPADDING",  (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",(0,0), (-1, -1), 5),
    ]))
    story.append(t_res)
    story.append(Spacer(1, 6))

    # ── DETALLE POR HABITACIÓN ──────────────────────────────────────────────
    story.append(Paragraph("DETALLE POR HABITACIÓN", s_sub))

    hab_header = ["Habitación", "Área (m²)", "Largo × Ancho", "Dirección",
                  "Pieza (m)", "Filas", "Perím. (m)", "Montantes (m)", "Tornillos"]
    hab_rows   = [hab_header]
    for i, h in enumerate(hab_info):
        area     = h["largo"] * h["ancho"]
        perim    = 2 * (h["largo"] + h["ancho"])
        dim_par  = h["ancho"] if h["orient"] == "largo" else h["largo"]
        dim_perp = h["largo"] if h["orient"] == "largo" else h["ancho"]
        mont     = (math.ceil(dim_par / pw) + 1) * dim_perp
        torn     = math.ceil(h["largo"] * h["ancho"] * total_screw / total_area) if total_area else 0
        torn     = math.ceil(area * (total_screw / total_area)) if total_area else 0
        dir_txt  = ("→ largo" if h["orient"] == "largo" else "↓ ancho") + (" (fijo)" if h["fijo"] else "")
        hab_rows.append([
            h["nombre"],
            f"{area:.2f}",
            f"{h['largo']}m × {h['ancho']}m",
            dir_txt,
            f"{h['dim_pieza']}m",
            str(h["filas"]),
            f"{perim:.1f}",
            f"{mont:.1f}",
            str(math.ceil(area * (total_screw / total_area)) if total_area else 0),
        ])

    col_ws_hab = [W*0.17, W*0.08, W*0.12, W*0.10,
                  W*0.09, W*0.07, W*0.10, W*0.13, W*0.10]
    t_hab = Table(hab_rows, colWidths=col_ws_hab, repeatRows=1)
    t_hab.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0),  NAVY),
        ("TEXTCOLOR",    (0, 0), (-1, 0),  colors.white),
        ("FONTNAME",     (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, -1), 7.5),
        ("ALIGN",        (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS",(0,1), (-1,-1),  [colors.white, LIGHT_GRAY]),
        ("GRID",         (0, 0), (-1, -1), 0.4, MID_GRAY),
        ("TOPPADDING",   (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
    ]))
    story.append(t_hab)
    story.append(Spacer(1, 6))

    # ── PLAN DE CORTE ───────────────────────────────────────────────────────
    story.append(Paragraph("PLAN DE CORTE — INSTRUCCIONES PASO A PASO", s_sub))
    story.append(Paragraph(
        "Seguí estas instrucciones en orden. Cada línea es una placa física. "
        "Los cortes están en orden de mayor a menor para minimizar el desperdicio.",
        s_body
    ))
    story.append(Spacer(1, 4))

    # Agrupar por largo de placa
    for L in [4, 5, 6]:
        bins_L = [b for b in plan if b["largo_placa"] == L]
        if not bins_L:
            continue

        story.append(Paragraph(f"Placas de {L} metros — {len(bins_L)} unidades", s_sub))

        corte_header = ["#", f"Placa {L}m", "Instrucción de corte", "Sobrante"]
        corte_rows   = [corte_header]

        for idx, bin_ in enumerate(bins_L, 1):
            # Construir instrucción
            partes = []
            for c in bin_["cortes"]:
                nom = habs[c["hab_idx"]]["nombre"] if c["hab_idx"] < len(habs) else "?"
                partes.append(f"{c['dim']}m → {nom}")
            instruccion = "  +  ".join(partes)

            libre = bin_["libre"]
            sobrante_str = f"{libre:.3f}m (descartar)" if libre > 0.005 else "sin sobrante"

            # Verificación: suma de cortes
            suma = sum(c["dim"] for c in bin_["cortes"])
            check = f"{suma:.3f}m usados de {L}m"

            corte_rows.append([
                str(idx),
                check,
                instruccion,
                sobrante_str,
            ])

        col_ws_c = [W*0.06, W*0.18, W*0.57, W*0.19]
        t_corte  = Table(corte_rows, colWidths=col_ws_c, repeatRows=1)
        t_corte.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0),  NAVY),
            ("TEXTCOLOR",     (0, 0), (-1, 0),  colors.white),
            ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), 7.5),
            ("ALIGN",         (0, 0), (1, -1),  "CENTER"),
            ("ALIGN",         (2, 1), (2, -1),  "LEFT"),
            ("ALIGN",         (3, 1), (3, -1),  "CENTER"),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, LIGHT_GRAY]),
            ("GRID",          (0, 0), (-1, -1), 0.4, MID_GRAY),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(t_corte)
        story.append(Spacer(1, 6))

    # ── PIE DE FIRMA ────────────────────────────────────────────────────────
    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=0.8, color=MID_GRAY, spaceAfter=8))

    fecha_hoy = date.today().strftime("%d/%m/%Y")
    firma_data = [[
        Paragraph(
            f"Este plan de corte fue generado el {fecha_hoy}. "
            f"El cliente recibió y aceptó las instrucciones.",
            s_footer
        ),
        Paragraph("Firma del cliente: ____________________________", s_footer),
    ]]
    t_firma = Table(firma_data, colWidths=[W * 0.6, W * 0.4])
    t_firma.setStyle(TableStyle([
        ("VALIGN",  (0, 0), (-1, -1), "BOTTOM"),
        ("ALIGN",   (1, 0), (1, 0),   "RIGHT"),
    ]))
    story.append(t_firma)

    doc.build(story)
    buf.seek(0)
    return buf.read()

# ─── SIDEBAR ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("⚙️ Configuración")

    st.subheader("Placas disponibles")
    usar_4 = st.checkbox("Placa 4 m", value=True)
    usar_5 = st.checkbox("Placa 5 m", value=True)
    usar_6 = st.checkbox("Placa 6 m", value=True)
    lens   = [l for l, u in [(4, usar_4), (5, usar_5), (6, usar_6)] if u]

    st.subheader("Medidas")
    pw   = st.number_input("Ancho de placa (m)",      value=0.20,  step=0.01,  min_value=0.05)
    kerf = st.number_input("Pérdida sierra (m)",       value=0.00001, step=0.0001, format="%.3f")
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
    st.session_state.habitaciones.append(
        {"nombre": f"Habitación {n}", "largo": 0.0, "ancho": 0.0, "fijo": False}
    )

def eliminar(i):
    st.session_state.habitaciones.pop(i)

for i, hab in enumerate(st.session_state.habitaciones):
    color = HAB_COLORS[i % len(HAB_COLORS)]
    col_color, col_form = st.columns([0.015, 0.985])
    with col_color:
        st.markdown(
            f'<div style="background:{color};width:6px;height:80px;'
            f'border-radius:4px;margin-top:4px"></div>',
            unsafe_allow_html=True,
        )
    with col_form:
        with st.container(border=True):
            c1, c2, c3, c4, c5 = st.columns([3, 1.5, 1.5, 2, 0.5])
            with c1:
                hab["nombre"] = st.text_input(
                    "Nombre", value=hab["nombre"], key=f"nom_{i}",
                    label_visibility="collapsed"
                )
            with c2:
                hab["largo"] = st.number_input(
                    "Largo (m)", value=hab["largo"], step=0.1,
                    min_value=0.1, key=f"lar_{i}"
                )
            with c3:
                hab["ancho"] = st.number_input(
                    "Ancho (m)", value=hab["ancho"], step=0.1,
                    min_value=0.1, key=f"anc_{i}"
                )
            with c4:
                hab["fijo"] = st.checkbox(
                    "Sentido fijo (→ largo)", value=hab["fijo"], key=f"fij_{i}"
                )
            with c5:
                st.write("")
                if st.button("🗑️", key=f"del_{i}",
                             disabled=len(st.session_state.habitaciones) <= 1):
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
    orient, dim_pieza, filas, piezas = orientacion_optima(
        h, i, lens, kerf, pw, h["fijo"]
    )
    hab_info.append({
        **h, "idx": i, "orient": orient,
        "dim_pieza": dim_pieza, "filas": filas, "piezas": piezas,
    })

todas_piezas = [p for h in hab_info for p in h["piezas"]]
plan = resolver_corte(todas_piezas, lens, kerf)

conteo = {4: 0, 5: 0, 6: 0}
for b in plan:
    conteo[b["largo_placa"]] += 1
total_placas = len(plan)

total_area  = sum(h["largo"] * h["ancho"] for h in habs)
total_perim = sum(2 * (h["largo"] + h["ancho"]) for h in habs)
total_mont, total_screw = 0.0, 0

for h in hab_info:
    dim_par  = h["ancho"] if h["orient"] == "largo" else h["largo"]
    dim_perp = h["largo"] if h["orient"] == "largo" else h["ancho"]
    total_mont  += (math.ceil(dim_par / ms) + 1) * dim_perp
    total_screw += math.ceil(h["largo"] * h["ancho"] * sm2)

costo_placas = sum(conteo[l] * precios[l] for l in [4, 5, 6])
costo_total  = costo_placas + total_perim * pperim + total_mont * pmont + total_screw * ptorn

metros_comprados  = sum(b["largo_placa"] for b in plan)
metros_usados     = sum(p["dim"] for p in todas_piezas)
metros_desperd    = metros_comprados - metros_usados
m2_comprados      = metros_comprados  * pw
m2_aprovechados   = metros_usados     * pw
m2_desperdiciados = metros_desperd    * pw
pct_desp = (metros_desperd / metros_comprados * 100) if metros_comprados > 0 else 0

# ─── MÉTRICAS ─────────────────────────────────────────────────────────────────

st.subheader("📊 Resumen")

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Área total",   f"{total_area:.2f} m²")
c2.metric("Total placas", f"{total_placas} un.")
c3.metric("Perimetral",   f"{total_perim:.1f} m")
c4.metric("Montantes",    f"{total_mont:.1f} m")
c5.metric("Tornillos",    f"{total_screw} un.")
if costo_total > 0:
    c6.metric("Costo estimado", f"${costo_total:,.0f}")

st.write("")
ca, cb, cc = st.columns(3)
ca.metric("✅ m² aprovechados",     f"{m2_aprovechados:.2f} m²",
          help="Metros cuadrados de placa que quedan instalados")
cb.metric("❌ m² de desperdicio",   f"{m2_desperdiciados:.2f} m²",
          delta=f"-{pct_desp:.1f}% del total comprado",
          delta_color="inverse",
          help="Metros cuadrados de placa que van al descarte (puntas)")
cc.metric("📦 m² totales comprados", f"{m2_comprados:.2f} m²",
          help="Total de placa a adquirir")

desc = " | ".join(f"{conteo[l]} × {l}m" for l in [4, 5, 6] if conteo[l] > 0)
st.info(f"**Mezcla óptima:** {desc}")

# ─── PLAN DE CORTE ────────────────────────────────────────────────────────────

st.subheader("✂️ Plan de corte")

leyenda_html = "".join(
    f'<span style="display:inline-flex;align-items:center;gap:5px;'
    f'margin-right:14px;font-size:13px">'
    f'<span style="width:12px;height:12px;border-radius:3px;'
    f'background:{HAB_COLORS[i % len(HAB_COLORS)]};display:inline-block"></span>'
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
    with st.expander(
        f"{LARGO_LABEL[L]} — {len(bins_L)} unidad{'es' if len(bins_L) > 1 else ''}"
    ):
        for idx, bin_ in enumerate(bins_L, 1):
            libre  = bin_["libre"]
            x_acum = 0.0
            segs_svg = ""
            for c in bin_["cortes"]:
                pct   = c["dim"] / L * 100
                color = HAB_COLORS[c["hab_idx"] % len(HAB_COLORS)]
                label = f"{c['dim']}m" if c["dim"] >= 0.5 else ""
                segs_svg += (
                    f'<rect x="{x_acum:.3f}%" width="{pct:.3f}%" height="100%" fill="{color}"/>'
                    f'<text x="{x_acum + pct/2:.3f}%" y="55%" dominant-baseline="middle" '
                    f'text-anchor="middle" fill="white" font-size="11" font-weight="600">{label}</text>'
                )
                x_acum += pct

            libre_pct    = libre / L * 100
            sobrante_svg = ""
            if libre > 0.01:
                sobrante_svg = (
                    f'<rect x="{x_acum:.3f}%" width="{libre_pct:.3f}%" height="100%" fill="#e5e7eb"/>'
                )
                if libre >= 0.3:
                    sobrante_svg += (
                        f'<text x="{x_acum + libre_pct/2:.3f}%" y="55%" '
                        f'dominant-baseline="middle" text-anchor="middle" '
                        f'fill="#9ca3af" font-size="10">{libre:.2f}m</text>'
                    )

            svg = (
                f'<svg width="100%" height="22" xmlns="http://www.w3.org/2000/svg" '
                f'style="border-radius:5px;overflow:hidden;border:0.5px solid #d1d5db">'
                f'{segs_svg}{sobrante_svg}</svg>'
            )
            libre_str = "sin sobrante" if libre < 0.01 else f"{libre:.3f} m libre"
            col_num, col_bar, col_info = st.columns([0.5, 8, 1.5])
            with col_num:
                st.markdown(f'<span style="font-size:11px;color:#9ca3af">#{idx}</span>',
                            unsafe_allow_html=True)
            with col_bar:
                st.markdown(svg, unsafe_allow_html=True)
            with col_info:
                st.markdown(f'<span style="font-size:11px;color:#9ca3af">{libre_str}</span>',
                            unsafe_allow_html=True)

# ─── DETALLE POR HABITACIÓN ───────────────────────────────────────────────────

st.subheader("🏠 Detalle por habitación")

filas_tabla = []
for i, h in enumerate(hab_info):
    area     = h["largo"] * h["ancho"]
    perim    = 2 * (h["largo"] + h["ancho"])
    dim_par  = h["ancho"] if h["orient"] == "largo" else h["largo"]
    dim_perp = h["largo"] if h["orient"] == "largo" else h["ancho"]
    mont     = (math.ceil(dim_par / ms) + 1) * dim_perp
    filas_tabla.append({
        "Habitación":    h["nombre"],
        "Área (m²)":     round(area, 2),
        "Dirección":     f"{'→ largo' if h['orient'] == 'largo' else '↓ ancho'}"
                         f"{'  (fijo)' if h['fijo'] else ''}",
        "Pieza (m)":     h["dim_pieza"],
        "Filas":         h["filas"],
        "Perím. (m)":    round(perim, 1),
        "Montantes (m)": round(mont, 1),
        "Tornillos":     math.ceil(area * sm2),
    })

st.dataframe(pd.DataFrame(filas_tabla), use_container_width=True, hide_index=True)

# ─── GENERACIÓN DE PDF ────────────────────────────────────────────────────────

st.divider()
st.subheader("📄 Generar reporte PDF")

descripcion = st.text_input(
    "Obra / Descripción (opcional)",
    placeholder="Ej: Casa González — Tucumán 123",
)

if st.button("📥 Descargar PDF", type="primary"):
    with st.spinner("Generando PDF..."):
        pdf_bytes = generar_pdf(
            habs, hab_info, plan, todas_piezas,
            total_area, total_perim, total_mont, total_screw,
            m2_comprados, m2_aprovechados, m2_desperdiciados, pct_desp,
            conteo, costo_total, sm2, descripcion,
        )
    st.download_button(
        label="⬇️ Hacer click para descargar",
        data=pdf_bytes,
        file_name=f"plan_corte_cielorrasos_{date.today().strftime('%Y%m%d')}.pdf",
        mime="application/pdf",
    )

st.caption(
    "Motor: bin-packing 1D mixto con reutilización global de sobrantes. "
    "Cada color representa una habitación."
)
