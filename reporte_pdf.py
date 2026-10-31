"""
reporte_pdf.py
----------------
Genera un resumen en PDF, prolijo, listo para mandarle al cliente por mail
con el resultado de su liquidación de IVA del período.

Usa la misma identidad visual oscura ("app-liquidar") que la interfaz web.
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

from calculo_iva import ResultadoIVA

# Misma paleta que templates/index.html — si cambian el tema ahí, replicarlo acá.
FONDO = colors.HexColor("#0A0E1A")
TARJETA = colors.HexColor("#131A2C")
BORDE = colors.HexColor("#232B42")
TEXTO = colors.HexColor("#E7EAF3")
TEXTO_SUAVE = colors.HexColor("#8891AA")
TEXTO_TENUE = colors.HexColor("#5A6483")
AMBAR = colors.HexColor("#F0B429")
AMBAR_TEXTO = colors.HexColor("#1A1200")

# Colores del logo "app-liquidar", letra por letra (igual que el CSS)
C2, C3, C4, C5, C6, C7 = (
    colors.HexColor("#4FA3E3"), colors.HexColor("#E55A4C"), colors.HexColor("#6FCF97"),
    colors.HexColor("#B37FEB"), colors.HexColor("#F0B429"), colors.HexColor("#4FA3E3"),
)

MARGEN = 2 * cm


def _hex(c):
    return c.hexval()[2:]


def _moneda(valor: float) -> str:
    texto = f"{abs(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    signo = "-" if valor < 0 else ""
    return f"{signo}$ {texto}"


def _fondo_y_tarjeta(canvas, doc):
    """Dibuja el fondo oscuro de página completa y la tarjeta redondeada del contenido."""
    canvas.saveState()
    ancho, alto = A4

    canvas.setFillColor(FONDO)
    canvas.rect(0, 0, ancho, alto, stroke=0, fill=1)

    margen_tarjeta = 1.2 * cm
    canvas.setFillColor(TARJETA)
    canvas.setStrokeColor(BORDE)
    canvas.setLineWidth(1)
    canvas.roundRect(
        margen_tarjeta, margen_tarjeta,
        ancho - 2 * margen_tarjeta, alto - 2 * margen_tarjeta,
        14, stroke=1, fill=1,
    )
    canvas.restoreState()


def generar_pdf(resultado: ResultadoIVA, cliente: str, path_salida: str, estudio: str = "Estudio Contable"):
    doc = SimpleDocTemplate(
        path_salida, pagesize=A4,
        topMargin=3 * cm, bottomMargin=3 * cm, leftMargin=MARGEN, rightMargin=MARGEN,
    )
    estilos = getSampleStyleSheet()

    estilo_logo = ParagraphStyle("Logo", parent=estilos["Title"], fontName="Helvetica-Bold",
                                  fontSize=20, textColor=TEXTO, alignment=0, spaceAfter=4)
    estilo_subtitulo = ParagraphStyle("Subtitulo", parent=estilos["Normal"], fontSize=10,
                                       textColor=TEXTO_SUAVE, spaceAfter=14, leading=14)
    estilo_cliente = ParagraphStyle("Cliente", parent=estilos["Normal"], fontSize=11,
                                     textColor=TEXTO, spaceAfter=16)
    estilo_normal_claro = ParagraphStyle("NormalClaro", parent=estilos["Normal"], fontSize=10,
                                          textColor=TEXTO, leading=14)
    estilo_chico = ParagraphStyle("Chico", parent=estilos["Normal"], fontSize=8, textColor=TEXTO_TENUE)

    # Logo con letras de colores, igual que en la web (app-liquidar)
    logo_html = (
        f'<font color="#{_hex(TEXTO)}">app</font>'
        f'<font color="#{_hex(TEXTO_SUAVE)}">-</font>'
        f'<font color="#{_hex(C2)}">liqu</font>'
        f'<font color="#{_hex(C3)}">i</font>'
        f'<font color="#{_hex(C4)}">d</font>'
        f'<font color="#{_hex(C5)}">a</font>'
        f'<font color="#{_hex(C6)}">r</font>'
    )

    story = [
        Paragraph(logo_html, estilo_logo),
        Paragraph(f"{estudio} · Liquidación de IVA — Período {resultado.periodo}", estilo_subtitulo),
        Paragraph(f"Cliente: <b>{cliente}</b>", estilo_cliente),
        Spacer(1, 4),
    ]

    filas = [
        ["Concepto", "Importe"],
        ["Total Ventas (Neto Gravado)", _moneda(resultado.total_ventas_neto)],
        ["Débito Fiscal (IVA Ventas)", _moneda(resultado.debito_fiscal)],
        ["Total Compras (Neto Gravado)", _moneda(resultado.total_compras_neto)],
        ["Crédito Fiscal (IVA Compras)", _moneda(resultado.credito_fiscal)],
        ["Saldo de IVA del período", _moneda(resultado.saldo)],
    ]

    tabla = Table(filas, colWidths=[9.5 * cm, 5.5 * cm])
    tabla.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (-1, 0), AMBAR),
        ("TEXTCOLOR", (0, 0), (-1, 0), AMBAR_TEXTO),

        ("FONTNAME", (0, 1), (-1, -2), "Helvetica"),
        ("TEXTCOLOR", (0, 1), (-1, -2), TEXTO),
        ("BACKGROUND", (0, 1), (-1, -2), TARJETA),

        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#2A2410")),
        ("TEXTCOLOR", (0, -1), (-1, -1), AMBAR),

        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDE),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(tabla)
    story.append(Spacer(1, 18))

    texto_resultado = (
        f"Saldo a <b>pagar</b> de $ {abs(resultado.saldo):,.2f}".replace(",", ".")
        if resultado.saldo > 0
        else f"Saldo técnico a <b>favor</b> de $ {abs(resultado.saldo):,.2f}".replace(",", ".")
        if resultado.saldo < 0
        else "Sin saldo a pagar ni a favor en este período."
    )
    story.append(Paragraph(texto_resultado, estilo_normal_claro))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f"Comprobantes procesados: {resultado.cant_comprobantes_ventas} ventas / "
        f"{resultado.cant_comprobantes_compras} compras.",
        estilo_chico,
    ))

    doc.build(story, onFirstPage=_fondo_y_tarjeta, onLaterPages=_fondo_y_tarjeta)
