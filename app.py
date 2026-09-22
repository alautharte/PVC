import streamlit as st
import math
import pandas as pd
from datetime import date
import io
import os

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
)
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.utils import ImageReader

st.set_page_config(
    page_title="Presupuestador Cielorrasos PVC",
    page_icon="🏠",
    layout="wide",
)

HAB_COLORS = [
    "#2563EB","#16A34A","#DC2626","#9333EA","#D97706",
    "#0891B2","#DB2777","#65A30D","#EA580C","#7C3AED"
]

NAVY       = colors.HexColor("#1B2A6B")
LIGHT_GRAY = colors.HexColor("#F3F4F6")
MID_GRAY   = colors.HexColor("#D1D5DB")
DARK_GRAY  = colors.HexColor("#374151")

# ── Constantes fijas de placa ──────────────────────────────────────────────
PW   = 0.20   # ancho de placa — fijo 20cm
KERF = 0.0    # pérdida por corte — 0 (la placa siempre tiene un poco más)

# ── Constantes de estructura (según especificaciones técnicas) ─────────────
LARGO_PERFIL     = 2.60
LARGO_MOLDURA    = 4.00
DESP_PVC         = 1.10
SEP_MAESTRAS     = 1.20
SEP_VELAS        = 1.00
SEP_MONTANTES    = 0.45
DIST_TARUGOS     = 0.50
TORNILLOS_T1_M2  = 12    # fijación de placas PVC
TORNILLOS_T2_M2  =  5    # unión de perfiles metálicos

st.title("🏠 Presupuestador Cielorrasos PVC")
st.caption("Optimización global de corte — mezcla de largos, colores por habitación, reutilización de sobrantes entre habitaciones")

# ═══════════════════════════════════════════════════════════════════
# MOTOR DE CORTE — evaluación global por largo
# ═══════════════════════════════════════════════════════════════════

def resolver_con_largo(piezas, L):
    """
    Resuelve el bin-packing 1D usando SOLO el largo L para todas las piezas.
    Retorna el plan, o None si alguna pieza no cabe.
    piezas: [{"dim": float, "hab_idx": int}]
    """
    sorted_p  = sorted(piezas, key=lambda x: -x["dim"])
    sobrantes = []
    plan      = []

    for p in sorted_p:
        if L < p["dim"] - 0.0001:
            return None  # pieza no cabe en este largo
        sobrantes.sort(key=lambda s: s["libre"])
        usado = False
        for s in sobrantes:
            if s["libre"] >= p["dim"] - 0.0001:
                bin_ = plan[s["plan_idx"]]
                bin_["cortes"].append({"dim": p["dim"], "hab_idx": p["hab_idx"]})
                s["libre"]    = max(0.0, s["libre"] - p["dim"])
                bin_["libre"] = s["libre"]
                usado = True
                break
        if not usado:
            libre = max(0.0, L - p["dim"])
            idx   = len(plan)
            plan.append({"largo_placa": L,
                         "cortes": [{"dim": p["dim"], "hab_idx": p["hab_idx"]}],
                         "libre": libre})
            sobrantes.append({"plan_idx": idx, "libre": libre})
    return plan


def elegir_mejor_plan(piezas, lens):
    """
    Evalúa cada largo disponible para el conjunto GLOBAL de piezas
    y elige el que minimiza el desperdicio total.
    Retorna el mejor plan.
    """
    mejor_plan  = None
    mejor_desp  = float("inf")
    metros_netos = sum(p["dim"] for p in piezas)

    for L in lens:
        plan = resolver_con_largo(piezas, L)
        if plan is None:
            continue
        metros_comp = sum(b["largo_placa"] for b in plan)
        desp        = metros_comp - metros_netos
        if desp < mejor_desp:
            mejor_desp  = desp
            mejor_plan  = plan

    return mejor_plan


def orientacion_optima(r, idx, lens):
    """Elige la orientación (largo/ancho) que minimiza el total de placas."""
    filas_largo  = math.ceil(r["ancho"] / PW)
    filas_ancho  = math.ceil(r["largo"] / PW)
    piezas_largo = [{"dim": r["largo"], "hab_idx": idx}] * filas_largo
    piezas_ancho = [{"dim": r["ancho"],  "hab_idx": idx}] * filas_ancho

    if r["fijo"]:
        return "largo", r["largo"], filas_largo, piezas_largo

    plan_largo = elegir_mejor_plan(piezas_largo, lens)
    plan_ancho  = elegir_mejor_plan(piezas_ancho,  lens)

    n_largo = len(plan_largo) if plan_largo else float("inf")
    n_ancho  = len(plan_ancho)  if plan_ancho  else float("inf")

    if n_largo <= n_ancho:
        return "largo", r["largo"], filas_largo, piezas_largo
    else:
        return "ancho",  r["ancho"],  filas_ancho,  piezas_ancho

# ═══════════════════════════════════════════════════════════════════
# CÁLCULO DE ESTRUCTURA
# ═══════════════════════════════════════════════════════════════════

def calcular_estructura(largo, ancho, altura):
    P = (largo + ancho) * 2

    SP = math.ceil(P / LARGO_PERFIL)

    lineas_maestras  = max(0, math.ceil(ancho / SEP_MAESTRAS) - 1)
    metros_maestras  = lineas_maestras * largo
    soleras_maestras = math.ceil(metros_maestras / LARGO_PERFIL) if metros_maestras > 0 else 0

    velas_por_linea = math.ceil(largo / SEP_VELAS) + 1
    total_velas     = velas_por_linea * lineas_maestras
    metros_velas    = total_velas * altura
    soleras_velas   = math.ceil(metros_velas / LARGO_PERFIL) if metros_velas > 0 else 0

    total_soleras = SP + soleras_maestras + soleras_velas

    lineas_montantes = math.ceil(largo / SEP_MONTANTES)
    metros_montantes = lineas_montantes * ancho
    total_montantes  = math.ceil(metros_montantes / LARGO_PERFIL)

    total_molduras = math.ceil(P / LARGO_MOLDURA)

    fijaciones_pared = math.ceil(P / DIST_TARUGOS)
    total_tarugos_n8 = fijaciones_pared + total_velas

    area    = largo * ancho
    total_t1 = math.ceil(area * TORNILLOS_T1_M2)
    total_t2 = math.ceil(area * TORNILLOS_T2_M2)

    return {
        "perimetro":        round(P, 2),
        "total_soleras":    total_soleras,
        "total_montantes":  total_montantes,
        "total_molduras":   total_molduras,
        "total_tarugos_n8": total_tarugos_n8,
        "total_t1":         total_t1,
        "total_t2":         total_t2,
    }

# ═══════════════════════════════════════════════════════════════════
# GENERADOR DE PDF
# ═══════════════════════════════════════════════════════════════════

def logo_imagen():
    try:
        base_dir  = os.path.dirname(os.path.abspath(__file__))
        logo_path = os.path.join(base_dir, "logo.png")
        if not os.path.exists(logo_path):
            return None
        with open(logo_path, "rb") as f:
            return ImageReader(io.BytesIO(f.read()))
    except Exception:
        return None


def generar_pdf(habs, hab_info, plan, todas_piezas,
                total_area, tot_sol, tot_mon, tot_mol, tot_tar,
                tot_t1, tot_t2, m2_comprados, m2_aprovechados,
                m2_desperdiciados, pct_desp, conteo, costo_total,
                estructuras, descripcion):

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=1.8*cm, rightMargin=1.8*cm,
                            topMargin=1.5*cm,  bottomMargin=2.2*cm)

    s_title  = ParagraphStyle("t", fontSize=15, textColor=NAVY,
                               fontName="Helvetica-Bold", spaceAfter=2)
    s_sub    = ParagraphStyle("s", fontSize=9,  textColor=NAVY,
                               fontName="Helvetica-Bold", spaceBefore=8, spaceAfter=3)
    s_body   = ParagraphStyle("b", fontSize=8,  textColor=DARK_GRAY,
                               fontName="Helvetica", spaceAfter=2)
    s_footer = ParagraphStyle("f", fontSize=7.5, textColor=DARK_GRAY,
                               fontName="Helvetica-Oblique", alignment=TA_CENTER)

    story = []
    W     = A4[0] - 3.6*cm
    fecha = date.today().strftime("%d/%m/%Y")

    # ── Encabezado ──────────────────────────────────────────────────
    logo_img = logo_imagen()
    if logo_img:
        from reportlab.platypus.flowables import Image as FImage
        logo_cell = FImage(logo_img, width=4.5*cm, height=1.8*cm)
    else:
        logo_cell = Paragraph("<b>LAUTHARTE MATERIALES</b>", s_title)

    t_head = Table([[logo_cell, Paragraph(f"<b>Fecha:</b> {fecha}", s_body)]],
                   colWidths=[W*0.65, W*0.35])
    t_head.setStyle(TableStyle([
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("ALIGN", (1,0),(1, 0),"RIGHT"),
    ]))
    story.append(t_head)
    story.append(HRFlowable(width="100%", thickness=1.5, color=NAVY, spaceAfter=5))

    if descripcion.strip():
        story.append(Paragraph(f"<b>Obra / Descripción:</b> {descripcion.strip()}", s_body))
        story.append(Spacer(1, 3))

    # ── Resumen de materiales ────────────────────────────────────────
    story.append(Paragraph("RESUMEN DE MATERIALES", s_sub))
    desc_mezcla = "  |  ".join(f"{conteo[l]} × {l}m" for l in [4,5,6] if conteo.get(l,0)>0)

    res_data = [
        ["Área total","Placas PVC","m² comprados","m² desperdicio","Desp. %"],
        [f"{total_area:.2f} m²", desc_mezcla,
         f"{m2_comprados:.2f} m²", f"{m2_desperdiciados:.2f} m²", f"{pct_desp:.1f}%"],
        ["Soleras (2.6m)","Montantes (2.6m)","Molduras PVC (4m)","Tarugos N°8","T1 (placas)","T2 (perfiles)"],
        [f"{tot_sol} un.", f"{tot_mon} un.", f"{tot_mol} un.",
         f"{tot_tar} un.", f"{tot_t1} un.", f"{tot_t2} un."],
    ]
    if costo_total > 0:
        res_data[0].append("Costo")
        res_data[1].append(f"${costo_total:,.0f}")

    cn = len(res_data[0])
    t_res = Table(res_data, colWidths=[W/cn]*cn)
    t_res.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,0), NAVY),
        ("BACKGROUND",    (0,2),(-1,2), NAVY),
        ("TEXTCOLOR",     (0,0),(-1,0), colors.white),
        ("TEXTCOLOR",     (0,2),(-1,2), colors.white),
        ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
        ("FONTNAME",      (0,2),(-1,2), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0),(-1,-1), 7.5),
        ("ALIGN",         (0,0),(-1,-1), "CENTER"),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
        ("ROWBACKGROUNDS",(0,1),(-1,1), [LIGHT_GRAY]),
        ("ROWBACKGROUNDS",(0,3),(-1,3), [LIGHT_GRAY]),
        ("GRID",          (0,0),(-1,-1), 0.4, MID_GRAY),
        ("TOPPADDING",    (0,0),(-1,-1), 4),
        ("BOTTOMPADDING", (0,0),(-1,-1), 4),
    ]))
    story.append(t_res)
    story.append(Spacer(1,5))

    # ── Detalle estructura por habitación ────────────────────────────
    story.append(Paragraph("DETALLE DE ESTRUCTURA POR HABITACIÓN", s_sub))
    eh    = ["Habitación","Dim.","Alt. susp.","Soleras","Montantes","Molduras","Tarugos N°8","T1","T2"]
    erows = [eh]
    for h in habs:
        e = estructuras[h["nombre"]]
        erows.append([h["nombre"], f"{h['largo']}×{h['ancho']}m",
                      f"{h.get('altura',0.30):.2f}m",
                      str(e["total_soleras"]), str(e["total_montantes"]),
                      str(e["total_molduras"]), str(e["total_tarugos_n8"]),
                      str(e["total_t1"]), str(e["total_t2"])])
    ew = [W*0.17,W*0.11,W*0.09,W*0.09,W*0.10,W*0.10,W*0.12,W*0.11,W*0.11]
    t_est = Table(erows, colWidths=ew, repeatRows=1)
    t_est.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,0), NAVY),
        ("TEXTCOLOR",     (0,0),(-1,0), colors.white),
        ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0),(-1,-1), 7.5),
        ("ALIGN",         (0,0),(-1,-1), "CENTER"),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [colors.white, LIGHT_GRAY]),
        ("GRID",          (0,0),(-1,-1), 0.4, MID_GRAY),
        ("TOPPADDING",    (0,0),(-1,-1), 4),
        ("BOTTOMPADDING", (0,0),(-1,-1), 4),
    ]))
    story.append(t_est)
    story.append(Spacer(1,5))

    # ── Detalle placas por habitación ────────────────────────────────
    story.append(Paragraph("DETALLE DE PLACAS PVC POR HABITACIÓN", s_sub))
    ph    = ["Habitación","Área (m²)","Dirección","Pieza (m)","Filas","Perím. (m)"]
    prows = [ph]
    for h in hab_info:
        area  = h["largo"]*h["ancho"]
        perim = 2*(h["largo"]+h["ancho"])
        dir_  = ("→ largo" if h["orient"]=="largo" else "↓ ancho") + (" (fijo)" if h["fijo"] else "")
        prows.append([h["nombre"], f"{area:.2f}", dir_,
                      f"{h['dim_pieza']}m", str(h["filas"]), f"{perim:.1f}"])
    pw2 = [W*0.22,W*0.13,W*0.20,W*0.13,W*0.10,W*0.12]
    # pad last col to fill width
    t_plac = Table(prows, colWidths=pw2, repeatRows=1)
    t_plac.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,0), NAVY),
        ("TEXTCOLOR",     (0,0),(-1,0), colors.white),
        ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0),(-1,-1), 7.5),
        ("ALIGN",         (0,0),(-1,-1), "CENTER"),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [colors.white, LIGHT_GRAY]),
        ("GRID",          (0,0),(-1,-1), 0.4, MID_GRAY),
        ("TOPPADDING",    (0,0),(-1,-1), 4),
        ("BOTTOMPADDING", (0,0),(-1,-1), 4),
    ]))
    story.append(t_plac)
    story.append(Spacer(1,5))

    # ── Plan de corte ────────────────────────────────────────────────
    story.append(Paragraph("PLAN DE CORTE — INSTRUCCIONES PASO A PASO", s_sub))
    story.append(Paragraph(
        "Cada fila es una placa física. Realizá los cortes en orden.",
        s_body))
    story.append(Spacer(1,3))

    for L in [4,5,6]:
        bins_L = [b for b in plan if b["largo_placa"]==L]
        if not bins_L:
            continue
        story.append(Paragraph(f"Placas de {L} metros — {len(bins_L)} unidades", s_sub))
        ch    = ["#", f"Placa {L}m", "Instrucción de corte", "Sobrante"]
        crows = [ch]
        for idx, bin_ in enumerate(bins_L, 1):
            partes = [f"{c['dim']}m → {habs[c['hab_idx']]['nombre']}" for c in bin_["cortes"]]
            suma   = sum(c["dim"] for c in bin_["cortes"])
            libre  = bin_["libre"]
            crows.append([str(idx), f"{suma:.2f}m / {L}m",
                          "  +  ".join(partes),
                          f"{libre:.3f}m (descartar)" if libre>0.005 else "sin sobrante"])
        cw2 = [W*0.06,W*0.16,W*0.59,W*0.19]
        t_c = Table(crows, colWidths=cw2, repeatRows=1)
        t_c.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,0), NAVY),
            ("TEXTCOLOR",     (0,0),(-1,0), colors.white),
            ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
            ("FONTSIZE",      (0,0),(-1,-1), 7.5),
            ("ALIGN",         (0,0),(1,-1), "CENTER"),
            ("ALIGN",         (2,1),(2,-1), "LEFT"),
            ("ALIGN",         (3,1),(3,-1), "CENTER"),
            ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
            ("ROWBACKGROUNDS",(0,1),(-1,-1), [colors.white, LIGHT_GRAY]),
            ("GRID",          (0,0),(-1,-1), 0.4, MID_GRAY),
            ("TOPPADDING",    (0,0),(-1,-1), 4),
            ("BOTTOMPADDING", (0,0),(-1,-1), 4),
        ]))
        story.append(t_c)
        story.append(Spacer(1,4))

    # ── Pie de firma ─────────────────────────────────────────────────
    story.append(Spacer(1,10))
    story.append(HRFlowable(width="100%", thickness=0.8, color=MID_GRAY, spaceAfter=6))
    t_firma = Table([[
        Paragraph(f"Este plan de corte fue generado el {fecha}. "
                  "El cliente recibió y aceptó las instrucciones.", s_footer),
        Paragraph("Firma del cliente: ____________________________", s_footer),
    ]], colWidths=[W*0.6, W*0.4])
    t_firma.setStyle(TableStyle([
        ("VALIGN",(0,0),(-1,-1),"BOTTOM"),
        ("ALIGN", (1,0),(1, 0),"RIGHT"),
    ]))
    story.append(t_firma)

    doc.build(story)
    buf.seek(0)
    return buf.read()

# ═══════════════════════════════════════════════════════════════════
# SIDEBAR
# ═══════════════════════════════════════════════════════════════════

with st.sidebar:
    st.header("⚙️ Configuración")

    st.subheader("Placas PVC disponibles")
    usar_4 = st.checkbox("Placa 4 m", value=True)
    usar_5 = st.checkbox("Placa 5 m", value=True)
    usar_6 = st.checkbox("Placa 6 m", value=True)
    lens   = [l for l, u in [(4,usar_4),(5,usar_5),(6,usar_6)] if u]

    st.caption(f"Ancho de placa: **{PW*100:.0f} cm** (fijo)  |  Pérdida por corte: **0 mm** (fijo)")

    st.subheader("Precios (opcional)")
    p4      = st.number_input("Placa 4 m ($ / un)",           value=0.0, step=100.0, format="%.0f")
    p5      = st.number_input("Placa 5 m ($ / un)",           value=0.0, step=100.0, format="%.0f")
    p6      = st.number_input("Placa 6 m ($ / un)",           value=0.0, step=100.0, format="%.0f")
    pperim  = st.number_input("Moldura PVC ($ / un)",          value=0.0, step=100.0, format="%.0f")
    psol    = st.number_input("Solera ($ / un)",               value=0.0, step=100.0, format="%.0f")
    pmont   = st.number_input("Montante ($ / un)",             value=0.0, step=100.0, format="%.0f")
    ptarug  = st.number_input("Tarugo N°8 ($ / un)",           value=0.0, step=10.0,  format="%.0f")
    ptorn_t1= st.number_input("Tornillo T1 — placas ($ / un)", value=0.0, step=10.0,  format="%.0f")
    ptorn_t2= st.number_input("Tornillo T2 — perfiles ($ / un)",value=0.0,step=10.0,  format="%.0f")
    precios = {4:p4, 5:p5, 6:p6}

# ═══════════════════════════════════════════════════════════════════
# HABITACIONES
# ═══════════════════════════════════════════════════════════════════

st.subheader("📐 Habitaciones")

if "habitaciones" not in st.session_state:
    st.session_state.habitaciones = [
        {"nombre":"Habitación 1","largo":3.5,"ancho":4.0,"altura":0.30,"fijo":False},
        {"nombre":"Habitación 2","largo":2.5,"ancho":3.0,"altura":0.30,"fijo":False},
    ]

def agregar():
    n = len(st.session_state.habitaciones)+1
    st.session_state.habitaciones.append(
        {"nombre":f"Habitación {n}","largo":0.0,"ancho":0.0,"altura":0.30,"fijo":False})

def eliminar(i):
    st.session_state.habitaciones.pop(i)

for i, hab in enumerate(st.session_state.habitaciones):
    color = HAB_COLORS[i%len(HAB_COLORS)]
    col_color, col_form = st.columns([0.015, 0.985])
    with col_color:
        st.markdown(
            f'<div style="background:{color};width:6px;height:88px;'
            f'border-radius:4px;margin-top:4px"></div>',
            unsafe_allow_html=True)
    with col_form:
        with st.container(border=True):
            c1,c2,c3,c4,c5,c6 = st.columns([2.5,1.3,1.3,1.3,1.8,0.5])
            with c1:
                hab["nombre"] = st.text_input("Nombre", value=hab["nombre"],
                                               key=f"nom_{i}", label_visibility="collapsed")
            with c2:
                hab["largo"]  = st.number_input("Largo (m)",  value=hab["largo"],
                                                 step=0.1, min_value=0.1, key=f"lar_{i}")
            with c3:
                hab["ancho"]  = st.number_input("Ancho (m)",  value=hab["ancho"],
                                                 step=0.1, min_value=0.1, key=f"anc_{i}")
            with c4:
                hab["altura"] = st.number_input("Alt. susp. (m)", value=hab.get("altura",0.30),
                                                 step=0.05, min_value=0.05, key=f"alt_{i}")
            with c5:
                hab["fijo"]   = st.checkbox("Sentido fijo (→ largo)",
                                             value=hab["fijo"], key=f"fij_{i}")
            with c6:
                st.write("")
                if st.button("🗑️", key=f"del_{i}",
                             disabled=len(st.session_state.habitaciones)<=1):
                    eliminar(i)
                    st.rerun()

st.button("➕ Agregar habitación", on_click=agregar)

# ═══════════════════════════════════════════════════════════════════
# CÁLCULO
# ═══════════════════════════════════════════════════════════════════

st.divider()

if not lens:
    st.error("Seleccioná al menos un largo de placa en la barra lateral.")
    st.stop()

habs = st.session_state.habitaciones

# Orientación por habitación
hab_info = []
for i, h in enumerate(habs):
    orient, dim_pieza, filas, piezas = orientacion_optima(h, i, lens)
    hab_info.append({**h, "idx":i, "orient":orient,
                     "dim_pieza":dim_pieza, "filas":filas, "piezas":piezas})

# Corte global: evaluar cada largo para el conjunto total de piezas
todas_piezas = [p for h in hab_info for p in h["piezas"]]
plan = elegir_mejor_plan(todas_piezas, lens)

conteo = {4:0, 5:0, 6:0}
for b in plan:
    conteo[b["largo_placa"]] += 1
total_placas = len(plan)

total_area = sum(h["largo"]*h["ancho"] for h in habs)

metros_comp  = sum(b["largo_placa"] for b in plan)
metros_us    = sum(p["dim"] for p in todas_piezas)
m2_comprados      = metros_comp * PW
m2_aprovechados   = metros_us   * PW
m2_desperdiciados = (metros_comp - metros_us) * PW
pct_desp = ((metros_comp - metros_us) / metros_comp * 100) if metros_comp > 0 else 0

# Estructura
estructuras = {h["nombre"]: calcular_estructura(h["largo"], h["ancho"], h.get("altura",0.30))
               for h in habs}

tot_sol  = sum(e["total_soleras"]   for e in estructuras.values())
tot_mon  = sum(e["total_montantes"] for e in estructuras.values())
tot_mol  = sum(e["total_molduras"]  for e in estructuras.values())
tot_tar  = sum(e["total_tarugos_n8"] for e in estructuras.values())
tot_t1   = sum(e["total_t1"]        for e in estructuras.values())
tot_t2   = sum(e["total_t2"]        for e in estructuras.values())
total_perim = sum(e["perimetro"] for e in estructuras.values())

costo_placas = sum(conteo[l]*precios[l] for l in [4,5,6])
costo_total  = (costo_placas + tot_mol*pperim + tot_sol*psol +
                tot_mon*pmont + tot_tar*ptarug + tot_t1*ptorn_t1 + tot_t2*ptorn_t2)

# ═══════════════════════════════════════════════════════════════════
# MÉTRICAS
# ═══════════════════════════════════════════════════════════════════

st.subheader("📊 Resumen")

c1,c2,c3,c4,c5,c6 = st.columns(6)
c1.metric("Área total",        f"{total_area:.2f} m²")
c2.metric("Placas PVC",        f"{total_placas} un.")
c3.metric("Soleras (2.6m)",    f"{tot_sol} un.")
c4.metric("Montantes (2.6m)",  f"{tot_mon} un.")
c5.metric("Molduras PVC (4m)", f"{tot_mol} un.")
c6.metric("Tarugos N°8",       f"{tot_tar} un.")

st.write("")
ca,cb,cc,cd,ce = st.columns(5)
ca.metric("✅ m² aprovechados",     f"{m2_aprovechados:.2f} m²")
cb.metric("❌ m² de desperdicio",   f"{m2_desperdiciados:.2f} m²",
          delta=f"-{pct_desp:.1f}%", delta_color="inverse")
cc.metric("📦 m² comprados",        f"{m2_comprados:.2f} m²")
cd.metric("🔩 Tornillos T1 (placas)",   f"{tot_t1} un.")
ce.metric("🔧 Tornillos T2 (perfiles)", f"{tot_t2} un.")

if costo_total > 0:
    st.metric("💰 Costo estimado total", f"${costo_total:,.0f}")

desc_mix = " | ".join(f"{conteo[l]} × {l}m" for l in [4,5,6] if conteo[l]>0)
st.info(f"**Mezcla óptima de placas:** {desc_mix}")

# ═══════════════════════════════════════════════════════════════════
# ESTRUCTURA POR HABITACIÓN
# ═══════════════════════════════════════════════════════════════════

st.subheader("🔧 Estructura por habitación")

filas_est = []
for h in habs:
    e = estructuras[h["nombre"]]
    filas_est.append({
        "Habitación":         h["nombre"],
        "Dimensiones":        f"{h['largo']}×{h['ancho']}m",
        "Alt. susp.":         f"{h.get('altura',0.30):.2f}m",
        "Soleras":            e["total_soleras"],
        "Montantes":          e["total_montantes"],
        "Molduras PVC":       e["total_molduras"],
        "Tarugos N°8":        e["total_tarugos_n8"],
        "Torn. T1 (placas)":  e["total_t1"],
        "Torn. T2 (perfiles)":e["total_t2"],
    })
st.dataframe(pd.DataFrame(filas_est), use_container_width=True, hide_index=True)

# ═══════════════════════════════════════════════════════════════════
# PLAN DE CORTE
# ═══════════════════════════════════════════════════════════════════

st.subheader("✂️ Plan de corte de placas")

leyenda_html = "".join(
    f'<span style="display:inline-flex;align-items:center;gap:5px;margin-right:14px;font-size:13px">'
    f'<span style="width:12px;height:12px;border-radius:3px;'
    f'background:{HAB_COLORS[i%len(HAB_COLORS)]};display:inline-block"></span>'
    f'{h["nombre"]}</span>'
    for i,h in enumerate(habs)
)
st.markdown(leyenda_html, unsafe_allow_html=True)
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
                label = f"{c['dim']}m" if c["dim"]>=0.4 else ""
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
                if libre >= 0.2:
                    sob += (f'<text x="{x_acum+lp/2:.3f}%" y="55%" dominant-baseline="middle" '
                            f'text-anchor="middle" fill="#9ca3af" font-size="10">{libre:.2f}m</text>')
            svg = (f'<svg width="100%" height="22" xmlns="http://www.w3.org/2000/svg" '
                   f'style="border-radius:5px;overflow:hidden;border:0.5px solid #d1d5db">'
                   f'{segs}{sob}</svg>')
            libre_str = "sin sobrante" if libre<0.001 else f"{libre:.3f} m libre"
            cn2, cb2, ci = st.columns([0.5,8,1.5])
            with cn2:
                st.markdown(f'<span style="font-size:11px;color:#9ca3af">#{idx}</span>',
                            unsafe_allow_html=True)
            with cb2:
                st.markdown(svg, unsafe_allow_html=True)
            with ci:
                st.markdown(f'<span style="font-size:11px;color:#9ca3af">{libre_str}</span>',
                            unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════
# DETALLE PLACAS POR HABITACIÓN
# ═══════════════════════════════════════════════════════════════════

st.subheader("🏠 Detalle de placas por habitación")

filas_plac = []
for h in hab_info:
    area  = h["largo"]*h["ancho"]
    perim = 2*(h["largo"]+h["ancho"])
    filas_plac.append({
        "Habitación":  h["nombre"],
        "Área (m²)":   round(area,2),
        "Dirección":   ("→ largo" if h["orient"]=="largo" else "↓ ancho")
                       +(" (fijo)" if h["fijo"] else ""),
        "Pieza (m)":   h["dim_pieza"],
        "Filas":       h["filas"],
        "Perím. (m)":  round(perim,1),
    })
st.dataframe(pd.DataFrame(filas_plac), use_container_width=True, hide_index=True)

# ═══════════════════════════════════════════════════════════════════
# PDF
# ═══════════════════════════════════════════════════════════════════

st.divider()
st.subheader("📄 Generar reporte PDF")

descripcion = st.text_input("Obra / Descripción (opcional)",
                             placeholder="Ej: Casa González — Tucumán 123")

if st.button("📥 Generar PDF", type="primary"):
    with st.spinner("Generando PDF..."):
        pdf_bytes = generar_pdf(
            habs, hab_info, plan, todas_piezas,
            total_area, tot_sol, tot_mon, tot_mol, tot_tar,
            tot_t1, tot_t2, m2_comprados, m2_aprovechados,
            m2_desperdiciados, pct_desp, conteo, costo_total,
            estructuras, descripcion,
        )
    st.download_button(
        label="⬇️ Descargar PDF",
        data=pdf_bytes,
        file_name=f"plan_corte_{date.today().strftime('%Y%m%d')}.pdf",
        mime="application/pdf",
    )

st.caption("Motor: evaluación global por largo — elige el largo que minimiza el desperdicio "
           "del conjunto completo de piezas. Ancho placa: 20cm fijo. Kerf: 0.")
