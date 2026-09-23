import streamlit as st
import math
import pandas as pd
from datetime import date
import io
import os

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
)
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.utils import ImageReader

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
SEP_MAESTRAS    = 1.20
SEP_VELAS       = 1.00
SEP_MONTANTES   = 0.50
DIST_TARUGOS    = 0.50
TORNILLOS_T1_M2 = 12
TORNILLOS_T2_M2 =  5

st.title("🏠 Presupuestador Cielorrasos PVC")
st.caption("Optimización global de corte — mezcla de largos, perfiles H, colores por habitación")

# ═══════════════════════════════════════════════════════════════════════════
# LÓGICA DE PERFIL H
# ═══════════════════════════════════════════════════════════════════════════

def dividir_con_h(dim, lens):
    """
    Cuando dim > max(lens), divide en segmentos equitativos usando largos disponibles.
    Retorna: (segmentos: [float], n_perfiles_h: int)
    """
    max_L = max(lens)
    if dim <= max_L:
        return [dim], 0

    n_tramos  = math.ceil(dim / max_L)
    segmentos = []
    restante  = dim
    for t in range(n_tramos):
        if t < n_tramos - 1:
            for L in sorted(lens, reverse=True):
                if L <= restante - (n_tramos - t - 1) * 0.1:
                    segmentos.append(L)
                    restante = round(restante - L, 4)
                    break
        else:
            segmentos.append(round(restante, 4))

    return segmentos, n_tramos - 1


def mejor_corte_h_opcional(dim, filas, hab_idx, todas_piezas_sin_hab, lens):
    """
    Para H opcional: prueba dividir dim en seg1+seg2 usando candidatos
    basados en sobrantes naturales del pool actual.
    Criterio de mejora: menos placas totales.
    Retorna (seg1, seg2, plan_mejorado) o None si ningún corte mejora.
    """
    # Plan base sin H para esta habitación
    piezas_base = [{"dim": dim, "hab_idx": hab_idx}] * filas
    plan_base   = elegir_mejor_plan(todas_piezas_sin_hab + piezas_base, lens)
    n_base      = len(plan_base)

    # Candidatos a seg1: sobrantes naturales = L - d para cada largo L y dim d del pool
    dims_pool = set(round(p["dim"], 3) for p in todas_piezas_sin_hab)
    candidatos = set()
    for L in lens:
        for d in dims_pool:
            sobra = round(L - d, 4)
            if 0.05 < sobra < dim - 0.05 and sobra <= max(lens):
                candidatos.add(sobra)
        # También agregar la mitad exacta
        candidatos.add(round(dim / 2, 4))

    mejor_plan = None
    mejor_n    = n_base  # solo mejorar si hay menos placas

    for seg1 in sorted(candidatos):
        seg2 = round(dim - seg1, 4)
        if seg2 < 0.05 or seg2 > max(lens) + 0.0001:
            continue
        if seg1 > max(lens) + 0.0001:
            continue

        piezas_h = []
        for _ in range(filas):
            piezas_h.append({"dim": seg1, "hab_idx": hab_idx})
            piezas_h.append({"dim": seg2, "hab_idx": hab_idx})

        plan_h = elegir_mejor_plan(todas_piezas_sin_hab + piezas_h, lens)
        if len(plan_h) < mejor_n:
            mejor_n    = len(plan_h)
            mejor_plan = (seg1, seg2, plan_h)

    return mejor_plan


def necesita_h(dim, lens):
    """True si la dimensión supera el largo máximo disponible."""
    return dim > max(lens) + 0.0001 if lens else False

# ═══════════════════════════════════════════════════════════════════════════
# MOTOR DE CORTE GLOBAL
# ═══════════════════════════════════════════════════════════════════════════

def resolver_con_largo(piezas, L):
    """Bin-packing 1D con largo L fijo. Piezas: [{"dim","hab_idx"}]"""
    sorted_p  = sorted(piezas, key=lambda x: -x["dim"])
    sobrantes = []
    plan      = []
    for p in sorted_p:
        if L < p["dim"] - 0.0001:
            return None
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
    """Evalúa cada largo y elige el que minimiza el desperdicio global."""
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


def calcular_piezas_l(largo_total, ancho_total, largo_reducido, ancho_reducido, idx, lens):
    """
    Calcula piezas para ambiente en L.
    Orientación A: placas corren en dirección largo_total.
      - ceil(ancho_total/PW) filas de largo_total  (incluye fila de esquina)
      - ceil(ancho_reducido/PW) filas de largo_reducido
    Orientación B: placas corren en dirección ancho_total (L rotada 90°).
    Elige la orientación con menor número de placas.
    """
    filas_A_l = math.ceil(ancho_total    / PW)
    filas_A_c = math.ceil(ancho_reducido / PW)
    piezas_A  = (
        [{"dim": largo_total,    "hab_idx": idx}] * filas_A_l +
        [{"dim": largo_reducido, "hab_idx": idx}] * filas_A_c
    )

    filas_B_l = math.ceil(largo_total    / PW)
    filas_B_c = math.ceil(largo_reducido / PW)
    piezas_B  = (
        [{"dim": ancho_total,    "hab_idx": idx}] * filas_B_l +
        [{"dim": ancho_reducido, "hab_idx": idx}] * filas_B_c
    )

    plan_A = elegir_mejor_plan(piezas_A, lens)
    plan_B = elegir_mejor_plan(piezas_B, lens)
    n_A = len(plan_A) if plan_A else float("inf")
    n_B = len(plan_B) if plan_B else float("inf")

    if n_A <= n_B:
        return "largo", piezas_A, filas_A_l, filas_A_c, largo_total, largo_reducido
    else:
        return "ancho", piezas_B, filas_B_l, filas_B_c, ancho_total, ancho_reducido


def diagrama_l_svg(lt, at, lr, ar, color):
    """SVG que muestra la forma L con medidas."""
    scale  = 120 / max(lt, at + ar)
    W_svg  = int(lt * scale) + 70
    H_svg  = int((at + ar) * scale) + 50
    x0, y0 = 30, 12
    wt = int(lt * scale)
    hat = int(at * scale)
    wlr = int(lr * scale)
    har = int(ar * scale)
    pts = (
        f"{x0},{y0} {x0+wt},{y0} {x0+wt},{y0+hat} "
        f"{x0+wlr},{y0+hat} {x0+wlr},{y0+hat+har} {x0},{y0+hat+har}"
    )
    c = color
    lines = (
        # largo total (arriba)
        '<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="{}" stroke-width="1"/>'.format(
            x0, y0-6, x0+wt, y0-6, c) +
        '<text x="{}" y="{}" fill="{}" font-size="10" text-anchor="middle">{}m</text>'.format(
            x0+wt//2, y0-9, c, lt) +
        # ancho total (derecha)
        '<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="{}" stroke-width="1"/>'.format(
            x0+wt+4, y0, x0+wt+4, y0+hat, c) +
        '<text x="{}" y="{}" fill="{}" font-size="10" text-anchor="middle">{}m</text>'.format(
            x0+wt+18, y0+hat//2+4, c, at) +
        # largo reducido (abajo)
        '<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="{}" stroke-width="1"/>'.format(
            x0, y0+hat+har+7, x0+wlr, y0+hat+har+7, c) +
        '<text x="{}" y="{}" fill="{}" font-size="10" text-anchor="middle">{}m</text>'.format(
            x0+wlr//2, y0+hat+har+19, c, lr) +
        # ancho reducido (derecha del tramo corto)
        '<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="{}" stroke-width="1"/>'.format(
            x0+wlr+4, y0+hat, x0+wlr+4, y0+hat+har, c) +
        '<text x="{}" y="{}" fill="{}" font-size="10" text-anchor="middle">{}m</text>'.format(
            x0+wlr+18, y0+hat+har//2+4, c, ar)
    )
    return (
        '<svg width="{}" height="{}" xmlns="http://www.w3.org/2000/svg">'
        '<polygon points="{}" fill="{}22" stroke="{}" stroke-width="2"/>'
        '{}</svg>'
    ).format(W_svg, H_svg, pts, c, c, lines)


def orientacion_optima(r, idx, lens_efectivos):
    """
    Elige orientación minimizando placas y desperdicio.
    Regla de prioridad:
      1. Si una orientación entra sin H y la otra no -> elegir la que no necesita H
      2. Si ambas entran sin H -> elegir la de menor desperdicio (evaluación global)
      3. Si ninguna entra sin H -> usar H en la que genere menos desperdicio
    """
    filas_largo = math.ceil(r["ancho"] / PW)
    filas_ancho  = math.ceil(r["largo"] / PW)

    largo_necesita_h = necesita_h(r["largo"], lens_efectivos)
    ancho_necesita_h  = necesita_h(r["ancho"],  lens_efectivos)

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
        return "largo", r["largo"], filas_largo, piezas, segs, n_h * filas_largo

    # Caso 1: una entra sin H y la otra no -> preferir sin H
    if not largo_necesita_h and ancho_necesita_h:
        segs = [r["largo"]]
        return "largo", r["largo"], filas_largo, piezas_simples(r["largo"], filas_largo, idx), segs, 0

    if largo_necesita_h and not ancho_necesita_h:
        segs = [r["ancho"]]
        return "ancho", r["ancho"], filas_ancho, piezas_simples(r["ancho"], filas_ancho, idx), segs, 0

    # Caso 2: ambas entran sin H -> elegir la de menor desperdicio
    if not largo_necesita_h and not ancho_necesita_h:
        p_largo = piezas_simples(r["largo"], filas_largo, idx)
        p_ancho  = piezas_simples(r["ancho"],  filas_ancho,  idx)
        plan_l = elegir_mejor_plan(p_largo, lens_efectivos)
        plan_a = elegir_mejor_plan(p_ancho,  lens_efectivos)
        n_l = len(plan_l) if plan_l else float("inf")
        n_a = len(plan_a) if plan_a else float("inf")
        if n_l <= n_a:
            return "largo", r["largo"], filas_largo, p_largo, [r["largo"]], 0
        else:
            return "ancho", r["ancho"], filas_ancho, p_ancho, [r["ancho"]], 0

    # Caso 3: ambas necesitan H -> evaluar con segmentos y elegir menor desperdicio
    p_largo = piezas_con_h(r["largo"], filas_largo, idx, lens_efectivos)
    p_ancho  = piezas_con_h(r["ancho"],  filas_ancho,  idx, lens_efectivos)
    plan_l = elegir_mejor_plan(p_largo, lens_efectivos)
    plan_a = elegir_mejor_plan(p_ancho,  lens_efectivos)
    n_l = len(plan_l) if plan_l else float("inf")
    n_a = len(plan_a) if plan_a else float("inf")
    if n_l <= n_a:
        segs, n_h = dividir_con_h(r["largo"], lens_efectivos)
        return "largo", r["largo"], filas_largo, p_largo, segs, n_h * filas_largo
    else:
        segs, n_h = dividir_con_h(r["ancho"], lens_efectivos)
        return "ancho", r["ancho"], filas_ancho, p_ancho, segs, n_h * filas_ancho

# ═══════════════════════════════════════════════════════════════════════════
# ESTRUCTURA
# ═══════════════════════════════════════════════════════════════════════════

def calcular_estructura(largo, ancho, altura, orientacion):
    if orientacion == "largo":
        dim_paralela, dim_transversal = largo, ancho
    else:
        dim_paralela, dim_transversal = ancho, largo

    P   = (largo + ancho) * 2
    SP  = math.ceil(P / LARGO_PERFIL)

    lineas_maestras  = max(0, math.ceil(dim_transversal / SEP_MAESTRAS) - 1)
    metros_maestras  = lineas_maestras * dim_paralela
    soleras_maestras = math.ceil(metros_maestras / LARGO_PERFIL) if metros_maestras > 0 else 0

    velas_por_linea = math.ceil(dim_paralela / SEP_VELAS) + 1
    total_velas     = velas_por_linea * lineas_maestras
    soleras_velas   = math.ceil(total_velas * altura / LARGO_PERFIL) if total_velas > 0 else 0

    total_soleras   = SP + soleras_maestras + soleras_velas

    lineas_montantes = math.ceil(dim_paralela / SEP_MONTANTES) + 1
    total_montantes  = math.ceil(lineas_montantes * dim_transversal / LARGO_PERFIL)

    total_molduras   = math.ceil(P / LARGO_MOLDURA)
    total_tarugos    = math.ceil(P / DIST_TARUGOS) + total_velas

    area     = largo * ancho
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
    except Exception:
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

    # Encabezado
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

    # Resumen
    story.append(Paragraph("RESUMEN DE MATERIALES", s_sub))
    desc_mix = "  |  ".join(f"{conteo[l]} × {l}m" for l in [4,5,6] if conteo.get(l,0)>0)
    h_txt = f"{tot_h_perfiles} H necesarios ({tot_h_varillas} varillas 4m)" if tot_h_perfiles > 0 else "No se requieren"

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

    # Detalle estructura
    story.append(Paragraph("DETALLE DE ESTRUCTURA POR HABITACIÓN", s_sub))
    eh = ["Habitación","Dim.","Alt.","Soleras","Montantes","Molduras","Tarugos","Perf. H","T1","T2"]
    erows = [eh]
    for h in habs:
        e  = estructuras[h["nombre"]]
        hi = next(x for x in hab_info if x["nombre"]==h["nombre"])
        erows.append([h["nombre"], f"{h['largo']}×{h['ancho']}m",
                      f"{h.get('altura',0.30):.2f}m",
                      str(e["total_soleras"]), str(e["total_montantes"]),
                      str(e["total_molduras"]), str(e["total_tarugos_n8"]),
                      str(hi["n_h"]) if hi["n_h"]>0 else "—",
                      str(e["total_t1"]), str(e["total_t2"])])
    ew = [W*0.15,W*0.10,W*0.07,W*0.08,W*0.09,W*0.09,W*0.09,W*0.09,W*0.08,W*0.08]
    t_est = Table(erows, colWidths=[sum(ew[:i+1])-sum(ew[:i]) for i in range(len(ew))], repeatRows=1)
    t_est.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),NAVY), ("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"), ("FONTSIZE",(0,0),(-1,-1),7),
        ("ALIGN",(0,0),(-1,-1),"CENTER"), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,LIGHT_GRAY]),
        ("GRID",(0,0),(-1,-1),0.4,MID_GRAY),
        ("TOPPADDING",(0,0),(-1,-1),3), ("BOTTOMPADDING",(0,0),(-1,-1),3),
    ]))
    story += [t_est, Spacer(1,5)]

    # Detalle placas
    story.append(Paragraph("DETALLE DE PLACAS PVC POR HABITACIÓN", s_sub))
    ph = ["Habitación","Área (m²)","Dirección","Dim. placa","Filas","Perím.","Perf. H","Varillas H (4m)"]
    prows = [ph]
    for h in hab_info:
        area  = h["largo"]*h["ancho"]
        perim = 2*(h["largo"]+h["ancho"])
        dir_  = ("→ largo" if h["orient"]=="largo" else "↓ ancho")+(" (fijo)" if h["fijo"] else "")
        varillas_h = math.ceil(h["n_h"] * h.get("dim_pieza_orig",h["dim_pieza"]) / LARGO_H) if h["n_h"]>0 else 0
        prows.append([h["nombre"], f"{area:.2f}", dir_,
                      f"{h['dim_pieza']}m", str(h["filas"]), f"{perim:.1f}",
                      str(h["n_h"]) if h["n_h"]>0 else "—",
                      str(varillas_h) if h["n_h"]>0 else "—"])
    pw2 = [W*0.18,W*0.10,W*0.16,W*0.10,W*0.08,W*0.10,W*0.10,W*0.13]
    t_plac = Table(prows, colWidths=pw2, repeatRows=1)
    t_plac.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),NAVY), ("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"), ("FONTSIZE",(0,0),(-1,-1),7.5),
        ("ALIGN",(0,0),(-1,-1),"CENTER"), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,LIGHT_GRAY]),
        ("GRID",(0,0),(-1,-1),0.4,MID_GRAY),
        ("TOPPADDING",(0,0),(-1,-1),4), ("BOTTOMPADDING",(0,0),(-1,-1),4),
    ]))
    story += [t_plac, Spacer(1,5)]

    # Plan de corte
    story.append(Paragraph("PLAN DE CORTE — INSTRUCCIONES PASO A PASO", s_sub))
    story.append(Paragraph("Cada fila es una placa física. ⚡ = requiere perfil H de unión.", s_body))
    story.append(Spacer(1,3))

    for L in [4,5,6]:
        bins_L = [b for b in plan if b["largo_placa"]==L]
        if not bins_L:
            continue
        story.append(Paragraph(f"Placas de {L} metros — {len(bins_L)} unidades", s_sub))

        s_cell   = ParagraphStyle("cell",  fontSize=7.5, textColor=DARK_GRAY,
                                   fontName="Helvetica",      leading=10)
        s_cell_h = ParagraphStyle("cellh", fontSize=7.5, textColor=colors.white,
                                   fontName="Helvetica-Bold", leading=10)

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

    # Pie
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

# ═══════════════════════════════════════════════════════════════════════════
# HABITACIONES
# ═══════════════════════════════════════════════════════════════════════════

st.subheader("📐 Habitaciones")

if "habitaciones" not in st.session_state:
    st.session_state.habitaciones = [
        {"nombre":"Habitación 1","largo":3.5,"ancho":4.0,"altura":0.30,"fijo":False,"forzar_h":False},
        {"nombre":"Habitación 2","largo":2.5,"ancho":3.0,"altura":0.30,"fijo":False,"forzar_h":False},
    ]

def agregar():
    n = len(st.session_state.habitaciones)+1
    st.session_state.habitaciones.append(
        {"nombre": f"Habitación {n}", "tipo": "rect",
         "largo": 0.1, "ancho": 0.1, "altura": 0.30,
         "fijo": False, "forzar_h": False})

def agregar_l():
    n = len(st.session_state.habitaciones)+1
    st.session_state.habitaciones.append(
        {"nombre": f"Ambiente L {n}", "tipo": "l",
         "largo_total": 4.0, "ancho_total": 1.5,
         "largo_reducido": 2.0, "ancho_reducido": 0.8,
         "altura": 0.30, "forzar_h": False})

def eliminar(i):
    st.session_state.habitaciones.pop(i)

for i, hab in enumerate(st.session_state.habitaciones):
    color    = HAB_COLORS[i%len(HAB_COLORS)]
    es_l     = hab.get("tipo") == "l"
    h_altura = "140px" if es_l else "100px"
    col_color, col_form = st.columns([0.015, 0.985])
    with col_color:
        st.markdown(
            f'<div style="background:{color};width:6px;height:{h_altura};'
            f'border-radius:4px;margin-top:4px"></div>',
            unsafe_allow_html=True)
    with col_form:
        with st.container(border=True):
            if es_l:
                # ── Habitación en L ──────────────────────────────────────
                r1c1, r1c2, r1c3, r1c4, r1c5, r1c6, r1c7 = st.columns([2.2,1.1,1.1,1.1,1.1,1.2,0.5])
                with r1c1:
                    hab["nombre"] = st.text_input("Nombre", value=hab["nombre"],
                                                   key=f"nom_{i}", label_visibility="collapsed")
                with r1c2:
                    hab["largo_total"]    = st.number_input("Largo total",    value=hab["largo_total"],
                                                             step=0.1, min_value=0.1, key=f"lt_{i}")
                with r1c3:
                    hab["ancho_total"]    = st.number_input("Ancho total",    value=hab["ancho_total"],
                                                             step=0.1, min_value=0.1, key=f"at_{i}")
                with r1c4:
                    hab["largo_reducido"] = st.number_input("Largo reducido", value=hab["largo_reducido"],
                                                             step=0.1, min_value=0.1, key=f"lr_{i}")
                with r1c5:
                    hab["ancho_reducido"] = st.number_input("Ancho reducido", value=hab["ancho_reducido"],
                                                             step=0.1, min_value=0.1, key=f"ar_{i}")
                with r1c6:
                    hab["altura"]  = st.number_input("Alt. susp.", value=hab.get("altura",0.30),
                                                      step=0.05, min_value=0.05, key=f"alt_{i}")
                with r1c7:
                    st.write("")
                    if st.button("🗑️", key=f"del_{i}",
                                 disabled=len(st.session_state.habitaciones)<=1):
                        eliminar(i)
                        st.rerun()
                # Diagrama SVG + info de orientación
                try:
                    svg_l = diagrama_l_svg(
                        hab["largo_total"], hab["ancho_total"],
                        hab["largo_reducido"], hab["ancho_reducido"], color)
                    st.markdown(svg_l, unsafe_allow_html=True)
                except Exception:
                    pass
            else:
                # ── Habitación rectangular normal ─────────────────────────
                c1,c2,c3,c4,c5,c6,c7 = st.columns([2.2,1.2,1.2,1.2,1.6,1.4,0.5])
                with c1:
                    hab["nombre"] = st.text_input("Nombre", value=hab["nombre"],
                                                   key=f"nom_{i}", label_visibility="collapsed")
                with c2:
                    hab["largo"]  = st.number_input("Largo (m)", value=hab["largo"],
                                                     step=0.1, min_value=0.1, key=f"lar_{i}")
                with c3:
                    hab["ancho"]  = st.number_input("Ancho (m)", value=hab["ancho"],
                                                     step=0.1, min_value=0.1, key=f"anc_{i}")
                with c4:
                    hab["altura"] = st.number_input("Alt. susp.", value=hab.get("altura",0.30),
                                                     step=0.05, min_value=0.05, key=f"alt_{i}")
                with c5:
                    hab["fijo"]     = st.checkbox("Sentido fijo (→)", value=hab["fijo"], key=f"fij_{i}")
                with c6:
                    hab["forzar_h"] = st.checkbox("🔗 Usar H", value=hab.get("forzar_h",False),
                                                   key=f"fh_{i}",
                                                   help="Activa perfil H. Si la placa no alcanza, es obligatorio. Si alcanza, el motor busca el corte óptimo que ahorre más placas.")
                with c7:
                    st.write("")
                    if st.button("🗑️", key=f"del_{i}",
                                 disabled=len(st.session_state.habitaciones)<=1):
                        eliminar(i)
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

# Paso 1: determinar orientación de cada habitación (sin H opcional aún)
hab_info = []
for i, h in enumerate(habs):
    es_l = h.get("tipo") == "l"

    if es_l:
        # ── Ambiente en L ────────────────────────────────────────────────
        lt  = h["largo_total"]
        at  = h["ancho_total"]
        lr  = h["largo_reducido"]
        ar  = h["ancho_reducido"]
        orient, piezas, filas_l, filas_c, dim_larga, dim_corta = calcular_piezas_l(
            lt, at, lr, ar, i, lens)
        filas      = filas_l + filas_c
        dim_pieza  = dim_larga   # dim principal para referencia
        segmentos  = [dim_larga]
        n_h        = 0
        area_l     = lt * at - (lt - lr) * ar   # área real de la L
        hab_info.append({
            **h, "idx": i, "orient": orient,
            "dim_pieza": dim_pieza, "dim_pieza_orig": dim_pieza,
            "filas": filas, "filas_l": filas_l, "filas_c": filas_c,
            "dim_larga": dim_larga, "dim_corta": dim_corta,
            "piezas": piezas, "segmentos": segmentos,
            "n_h": 0, "varillas_h": 0,
            "auto_h": False, "h_opcional_aplicado": False,
            "area_real": round(area_l, 4),
            "largo": lt, "ancho": at,  # para compatibilidad con estructura
            "fijo": False, "forzar_h": False,
        })
    else:
        # ── Habitación rectangular normal ─────────────────────────────────
        orient, dim_pieza, filas, piezas, segmentos, n_h = orientacion_optima(h, i, lens)

        if not usar_h:
            if necesita_h(dim_pieza, lens):
                st.warning(f"⚠️ **{h['nombre']}**: {dim_pieza}m supera el largo máximo "
                           f"disponible ({max(lens)}m). Activá el perfil H en la barra lateral.")
            n_h       = 0
            segmentos = [dim_pieza]
            piezas    = [{"dim": dim_pieza, "hab_idx": i}] * filas

        hab_info.append({
            **h, "idx": i, "orient": orient,
            "dim_pieza": dim_pieza, "dim_pieza_orig": dim_pieza,
            "filas": filas, "piezas": piezas,
            "segmentos": segmentos, "n_h": n_h,
            "varillas_h": n_h,
            "auto_h": necesita_h(dim_pieza, lens) and usar_h,
            "h_opcional_aplicado": False,
            "area_real": round(h["largo"] * h["ancho"], 4),
        })

# Paso 2: para habitaciones con "Usar H" opcional (no obligatorio),
# buscar el corte que maximiza el ahorro de placas en el contexto global.
# Se evalúan en orden de mayor a menor dimensión (las más grandes tienen más impacto).
if usar_h:
    candidatas = [
        h for h in hab_info
        if h.get("forzar_h", False) and h["n_h"] == 0  # H opcional activado, no obligatorio
    ]
    # Ordenar por dimensión descendente para evaluar primero las de mayor impacto
    candidatas.sort(key=lambda h: h["dim_pieza"], reverse=True)

    for hc in candidatas:
        # Piezas de todas las otras habitaciones (ya resueltas)
        otras_piezas = [
            p for h in hab_info
            if h["idx"] != hc["idx"]
            for p in h["piezas"]
        ]
        resultado = mejor_corte_h_opcional(
            hc["dim_pieza"], hc["filas"], hc["idx"], otras_piezas, lens
        )
        if resultado:
            seg1, seg2, _ = resultado
            nuevas_piezas = []
            for _ in range(hc["filas"]):
                nuevas_piezas.append({"dim": seg1, "hab_idx": hc["idx"]})
                nuevas_piezas.append({"dim": seg2, "hab_idx": hc["idx"]})
            # Actualizar esta habitación en hab_info
            for h in hab_info:
                if h["idx"] == hc["idx"]:
                    h["piezas"]               = nuevas_piezas
                    h["segmentos"]            = [seg1, seg2]
                    h["n_h"]                  = hc["filas"]
                    h["varillas_h"]           = hc["filas"]
                    h["h_opcional_aplicado"]  = True
                    break

todas_piezas = [p for h in hab_info for p in h["piezas"]]
plan = elegir_mejor_plan(todas_piezas, lens)

conteo = {4:0, 5:0, 6:0}
for b in plan:
    conteo[b["largo_placa"]] += 1
total_placas = len(plan)
total_area   = sum(h["largo"]*h["ancho"] for h in habs)

metros_comp       = sum(b["largo_placa"] for b in plan)
metros_us         = sum(p["dim"] for p in todas_piezas)
m2_comprados      = metros_comp * PW
m2_aprovechados   = metros_us   * PW
m2_desperdiciados = (metros_comp - metros_us) * PW
pct_desp = ((metros_comp-metros_us)/metros_comp*100) if metros_comp>0 else 0

# Perfiles H totales
tot_h_perfiles = sum(h["n_h"] for h in hab_info)
# Varillas de perfil H a comprar (cada perfil H mide 4m, necesita cubrir el largo de la habitación)
tot_h_varillas = sum(
    math.ceil(h["n_h"] * h["dim_pieza"] / LARGO_H) for h in hab_info if h["n_h"] > 0
)

estructuras = {h["nombre"]: calcular_estructura(
    h["largo"], h["ancho"], h.get("altura",0.30), h["orient"]) for h in hab_info}

tot_sol  = sum(e["total_soleras"]   for e in estructuras.values())
tot_mon  = sum(e["total_montantes"] for e in estructuras.values())
tot_mol  = sum(e["total_molduras"]  for e in estructuras.values())
tot_tar  = sum(e["total_tarugos_n8"] for e in estructuras.values())
tot_t1   = sum(e["total_t1"]        for e in estructuras.values())
tot_t2   = sum(e["total_t2"]        for e in estructuras.values())
total_perim = sum(e["perimetro"] for e in estructuras.values())

costo_placas = sum(conteo[l]*precios[l] for l in [4,5,6])
costo_h      = tot_h_varillas * p_h
costo_total  = (costo_placas + costo_h + tot_mol*pperim + tot_sol*psol +
                tot_mon*pmont + tot_tar*ptarug + tot_t1*ptorn_t1 + tot_t2*ptorn_t2)

# ═══════════════════════════════════════════════════════════════════════════
# MÉTRICAS
# ═══════════════════════════════════════════════════════════════════════════

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
ca.metric("✅ m² aprovechados",     f"{m2_aprovechados:.2f} m²")
cb.metric("❌ m² desperdicio",      f"{m2_desperdiciados:.2f} m²",
          delta=f"-{pct_desp:.1f}%", delta_color="inverse")
cc.metric("📦 m² comprados",        f"{m2_comprados:.2f} m²")
cd.metric("🔩 Tornillos T1",        f"{tot_t1} un.")
ce.metric("🔧 Tornillos T2",        f"{tot_t2} un.")
cf.metric("🔗 Perfiles H",
          f"{tot_h_perfiles} un." if tot_h_perfiles>0 else "No necesario",
          help=f"{tot_h_varillas} varillas de 4m a comprar" if tot_h_varillas>0 else "")

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
    e = estructuras[h["nombre"]]
    varillas = math.ceil(h["n_h"] * h["dim_pieza"] / LARGO_H) if h["n_h"]>0 else 0
    h_txt = f"{h['n_h']} H ({varillas} var.)" if h["n_h"]>0 else "—"
    filas_est.append({
        "Habitación":          h["nombre"],
        "Dimensiones":         f"{h['largo']}×{h['ancho']}m",
        "Alt. susp.":          f"{h.get('altura',0.30):.2f}m",
        "Soleras":             e["total_soleras"],
        "Montantes":           e["total_montantes"],
        "Molduras PVC":        e["total_molduras"],
        "Tarugos N°8":         e["total_tarugos_n8"],
        "Perfil H":            h_txt,
        "Torn. T1":            e["total_t1"],
        "Torn. T2":            e["total_t2"],
    })
st.dataframe(pd.DataFrame(filas_est), use_container_width=True, hide_index=True)

# ═══════════════════════════════════════════════════════════════════════════
# PLAN DE CORTE
# ═══════════════════════════════════════════════════════════════════════════

st.subheader("✂️ Plan de corte de placas")

# Leyenda habitaciones
leyenda = "".join(
    f'<span style="display:inline-flex;align-items:center;gap:5px;margin-right:14px;font-size:13px">'
    f'<span style="width:12px;height:12px;border-radius:3px;background:{HAB_COLORS[i%len(HAB_COLORS)]};display:inline-block"></span>'
    f'{h["nombre"]}{"  🔗H" if h["n_h"]>0 else ""}</span>'
    for i,h in enumerate(hab_info)
)
st.markdown(leyenda, unsafe_allow_html=True)

# Avisos H por habitación
for h in hab_info:
    if h["n_h"] > 0:
        varillas = math.ceil(h["n_h"] * h["dim_pieza"] / LARGO_H)
        segs_txt = " + ".join(f"{s}m" for s in h["segmentos"])
        if h["auto_h"]:
            origen = "obligatorio — dimensión supera largo máximo de placa"
            color  = "🔴"
        elif h.get("h_opcional_aplicado"):
            origen = "opcional aplicado — el motor encontró un corte que ahorra placas"
            color  = "🟢"
        else:
            origen = "opcional"
            color  = "🔗"
        st.info(
            f"{color} **{h['nombre']}** — H {origen} | "
            f"corte por fila: {segs_txt} | "
            f"{h['n_h']} uniones | "
            f"{varillas} varillas H de 4m"
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
    area  = h.get("area_real", h["largo"]*h["ancho"])
    perim = 2*(h["largo"]+h["ancho"])
    varillas = math.ceil(h["n_h"] * h["dim_pieza"] / LARGO_H) if h["n_h"]>0 else 0
    if es_l:
        segs_txt = (f"{h['dim_larga']}m ({h['filas_l']} filas) + "
                    f"{h['dim_corta']}m ({h['filas_c']} filas)")
        dir_txt  = f"→ largo total" if h["orient"]=="largo" else "↓ ancho total"
        tipo_txt = "📐 L"
    else:
        segs_txt = " + ".join(f"{s}m" for s in h["segmentos"]) if h["n_h"]>0 else f"{h['dim_pieza']}m"
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
        "Perfil H":       f"{h['n_h']} H / {varillas} var." if h["n_h"]>0 else "—",
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

st.caption("Motor: evaluación global por largo. Perfil H: división equitativa de piezas largas. "
           "Ancho placa: 20 cm fijo.")
