"""
f931_pdf.py
-------------
PDF con el modelo de presentación del F.931 del período: datos del empleador,
nómina con remuneraciones, aportes y contribuciones por empleado, y resumen de
lo que se estima a pagar. Fondo blanco, pensado para imprimir o mandar por mail.

Es un PAPEL DE TRABAJO: la declaración jurada se presenta desde SICOSS /
Declaración en línea (para eso está el TXT de importación).
"""

from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from f931_calculo import TIPOS_EMPLEADO, TIPOS_EMPLEADOR

AZUL = colors.HexColor("#1F3A5F")
GRIS = colors.HexColor("#F2F4F8")
BORDE = colors.HexColor("#C9D0DD")
TEXTO_SUAVE = colors.HexColor("#5A6483")


def _m(valor: float) -> str:
    texto = f"{abs(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-" if valor < 0 else "") + texto


def _pie(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(TEXTO_SUAVE)
    ancho, _ = landscape(A4)
    canvas.drawString(1.5 * cm, 1 * cm, "Papel de trabajo generado por app-liquidar. Estimación a controlar contra SICOSS; no es la declaración jurada.")
    canvas.drawRightString(ancho - 1.5 * cm, 1 * cm, f"Página {doc.page}")
    canvas.restoreState()


def generar_pdf_f931(resultado, cliente, empleador, path_salida, estudio="Estudio Contable"):
    """
    resultado: ResultadoLiquidacion (f931_calculo); cliente: objeto con razon_social y cuit;
    empleador: dict (EmpleadorF931.como_dict()).
    """
    doc = SimpleDocTemplate(
        path_salida, pagesize=landscape(A4),
        topMargin=1.4 * cm, bottomMargin=1.6 * cm, leftMargin=1.5 * cm, rightMargin=1.5 * cm,
        title=f"F.931 {resultado.periodo} - {cliente.razon_social}",
    )
    est = getSampleStyleSheet()
    titulo = ParagraphStyle("t", parent=est["Title"], fontName="Helvetica-Bold", fontSize=17, textColor=AZUL, alignment=0, spaceAfter=2)
    sub = ParagraphStyle("s", parent=est["Normal"], fontSize=9.5, textColor=TEXTO_SUAVE, leading=13, spaceAfter=8)
    h2 = ParagraphStyle("h2", parent=est["Heading2"], fontName="Helvetica-Bold", fontSize=11, textColor=AZUL, spaceBefore=10, spaceAfter=5)
    chico = ParagraphStyle("c", parent=est["Normal"], fontSize=8, textColor=TEXTO_SUAVE, leading=11)

    t = resultado.totales
    anio, mes = resultado.periodo.split("-")
    story = [
        Paragraph(f"Formulario 931 — Período {mes}/{anio}", titulo),
        Paragraph(f"{estudio} · Declaración de aportes y contribuciones de la seguridad social (modelo de presentación)", sub),
    ]

    # --- Empleador ---
    datos = [
        ["Empleador", cliente.razon_social, "CUIT", cliente.cuit],
        ["Tipo de empleador", TIPOS_EMPLEADOR.get(empleador.get("tipo_empleador"), "-"), "Actividad / Zona", f"{empleador.get('actividad') or '-'} / {empleador.get('zona') or '0'}"],
        ["Provincia/localidad", empleador.get("provincia") or "-", "Empleados informados", str(len(resultado.empleados))],
    ]
    tabla_datos = Table(datos, colWidths=[3.6 * cm, 12.5 * cm, 3.8 * cm, 6.5 * cm])
    tabla_datos.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9), ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"), ("BACKGROUND", (0, 0), (-1, -1), GRIS),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDE), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story += [tabla_datos, Paragraph("Nómina y cálculo por empleado", h2)]

    # --- Nómina ---
    cab = ["CUIL", "Apellido y nombre", "Tipo", "Días", "Rem. total", "Rem. imponible", "Aporte SIPA\n+ PAMI", "Aporte\nobra social", "Contrib.\nSS", "Contrib.\nobra social", "ART", "SCVO"]
    filas = [cab]
    for r in resultado.empleados:
        e = r.empleado
        filas.append([
            e["cuil"], e["apellido_nombre"][:34], {"comun": "Común", "jubilado": "Jubilado", "parcial": "Parcial"}.get(e.get("tipo"), ""),
            str(r.remuneracion.get("dias") or 30),
            _m(r.rem_total), _m(r.imponible), _m(r.aporte_jubilacion + r.aporte_inssjp), _m(r.aporte_os),
            _m(r.contrib_ss), _m(r.contrib_os), _m(r.art), _m(r.scvo),
        ])
    filas.append(["", "TOTALES", "", "", _m(t["rem_total"]), _m(t["imponible"]), _m(t["aportes_ss"]), _m(t["aporte_os"]),
                  _m(t["contrib_ss"]), _m(t["contrib_os"]), _m(t["art"]), _m(t["scvo"])])
    anchos = [2.5, 4.8, 1.9, 0.9] + [2.2] * 8
    tabla = Table(filas, colWidths=[a * cm for a in anchos], repeatRows=1)
    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), AZUL), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 7.6),
        ("ALIGN", (3, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, GRIS]),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#E1E7F2")), ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDE), ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]))
    story += [tabla, Paragraph("Resumen a pagar (estimado)", h2)]

    # --- Resumen ---
    resumen = [
        ["Concepto", "Importe ($)"],
        ["Aportes del trabajador — Jubilación (SIPA)", _m(t["aporte_jubilacion"])],
        ["Aportes del trabajador — INSSJP (PAMI)", _m(t["aporte_inssjp"])],
        ["Contribuciones patronales — Seguridad social (neta de detracción)", _m(t["contrib_ss"])],
        ["Subtotal seguridad social", _m(t["total_ss"])],
        ["Aportes del trabajador — Obra social", _m(t["aporte_os"])],
        ["Contribuciones patronales — Obra social", _m(t["contrib_os"])],
        ["Subtotal obra social", _m(t["total_os"])],
        ["ART (Ley de Riesgos del Trabajo)", _m(t["art"])],
        ["Seguro de Vida Obligatorio", _m(t["scvo"])],
        ["TOTAL ESTIMADO A PAGAR", _m(t["total"])],
    ]
    tr = Table(resumen, colWidths=[13 * cm, 4.5 * cm], hAlign="LEFT")
    tr.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), AZUL), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("FONTNAME", (0, 4), (-1, 4), "Helvetica-Bold"), ("FONTNAME", (0, 7), (-1, 7), "Helvetica-Bold"),
        ("BACKGROUND", (0, 4), (-1, 4), GRIS), ("BACKGROUND", (0, 7), (-1, 7), GRIS),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"), ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#E1E7F2")),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDE), ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]))
    story.append(tr)

    notas = [
        f"Base imponible usada: mínima $ {_m(resultado.base_minima)} · máxima $ {_m(resultado.base_maxima)} (ANSES).",
        f"Detracción aplicada (Ley 27.541): $ {_m(t['detraccion'])}"
        + (f", de la cual $ {_m(resultado.detraccion_adicional_aplicada)} es la adicional para empleadores de hasta 25 trabajadores." if resultado.detraccion_adicional_aplicada else "."),
    ] + list(resultado.avisos)
    for r in resultado.empleados:
        for a in r.avisos:
            notas.append(f"{r.empleado['apellido_nombre']}: {a}")
    story.append(Spacer(1, 8))
    for n in notas:
        story.append(Paragraph("• " + n, chico))
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"Generado el {date.today():%d/%m/%Y}. Alícuotas y topes según los parámetros cargados en la app; verificar vigencia antes de presentar.", chico))

    doc.build(story, onFirstPage=_pie, onLaterPages=_pie)
