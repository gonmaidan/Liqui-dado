"""
escaner.py
------------
Carga de comprobantes escaneando el código QR de AFIP de la factura (foto
o PDF), en vez de tipear todo a mano en el Excel.

Flujo:
  1. GET /clientes/<id>/escanear   -> el estudio elige venta/compra y la
     alícuota de IVA de las facturas que va a escanear ahora (se recuerda
     para la próxima, así no hay que re-elegirla en cada una), y sube la
     foto/PDF.
  2. POST /clientes/<id>/escanear  -> se lee el QR (fecha, CUIT, tipo,
     punto de venta, número, importe total) y se muestra una pantalla
     para revisar/completar: razón social (se memoriza por CUIT, así que
     solo se pide la primera vez) y percepciones de IVA/IIBB si esa
     factura tiene (si no, se dejan en 0).
  3. POST /clientes/<id>/escanear/confirmar -> se calcula el neto gravado
     y el IVA restando las percepciones del importe total del QR y
     aplicando la alícuota elegida, y se guarda. No hace falta tipear ni
     el neto ni el IVA a mano.

Los comprobantes así cargados se combinan con los subidos por Excel al
generar la liquidación (ver /generar en app.py).
"""

import io
import os
import uuid
from datetime import datetime
from itertools import zip_longest

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, send_file
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Comprobante, ContraparteConocida, PercepcionIibb
from auth import estudio_required
from clientes import _cliente_del_estudio_o_404
from qr_scanner import leer_qr_de_archivo, calcular_neto_e_iva, QrInvalido, PercepcionesSuperanElTotal, TIPO_DOC_CUIT
from afip_libro_iva import ALICUOTAS_AFIP
from percepciones_pdf import detectar as detectar_percepciones, TOLERANCIA_REDONDEO

escaner_bp = Blueprint("escaner", __name__, url_prefix="/clientes/<int:cliente_id>")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TMP_DIR = os.path.join(BASE_DIR, "_tmp_web")
os.makedirs(TMP_DIR, exist_ok=True)

EXTENSIONES_PERMITIDAS = {".jpg", ".jpeg", ".png", ".pdf"}
ALICUOTAS_DISPONIBLES = sorted(ALICUOTAS_AFIP.keys())  # [0.0, 2.5, 5.0, 10.5, 21.0, 27.0]


def _buscar_razon_social_conocida(cuit: str) -> str | None:
    conocida = ContraparteConocida.query.filter_by(
        estudio_id=current_user.id, cuit=cuit
    ).first()
    return conocida.razon_social if conocida else None


def _memorizar_contraparte(cuit: str, razon_social: str):
    conocida = ContraparteConocida.query.filter_by(
        estudio_id=current_user.id, cuit=cuit
    ).first()
    if conocida:
        conocida.razon_social = razon_social
    else:
        db.session.add(
            ContraparteConocida(estudio_id=current_user.id, cuit=cuit, razon_social=razon_social)
        )


MAX_ARCHIVOS_POR_LOTE = 20  # el plan gratis de Render tiene poca memoria y tiempo por request


@escaner_bp.route("/escanear", methods=["GET", "POST"])
@estudio_required
def escanear(cliente_id):
    cliente = _cliente_del_estudio_o_404(cliente_id)

    if request.method == "GET":
        alicuota_sugerida = session.get("ultima_alicuota", "21.0")
        tipo_sugerido = session.get("ultimo_tipo", "")
        return render_template(
            "escanear_subir.html",
            cliente=cliente,
            alicuotas=ALICUOTAS_DISPONIBLES,
            alicuota_sugerida=alicuota_sugerida,
            tipo_sugerido=tipo_sugerido,
            max_archivos=MAX_ARCHIVOS_POR_LOTE,
        )

    tipo = request.form.get("tipo", "")
    alicuota = request.form.get("alicuota", "")
    archivos = [a for a in request.files.getlist("archivos") if a and a.filename]

    if tipo not in ("venta", "compra"):
        flash("Elegí si son comprobantes de venta o de compra.")
        return redirect(url_for("escaner.escanear", cliente_id=cliente.id))

    try:
        alicuota_pct = float(alicuota)
        if alicuota_pct not in ALICUOTAS_DISPONIBLES:
            raise ValueError
    except ValueError:
        flash("Elegí la alícuota de IVA de las facturas que vas a escanear.")
        return redirect(url_for("escaner.escanear", cliente_id=cliente.id))

    if not archivos:
        flash("Subí al menos una foto o PDF de factura.")
        return redirect(url_for("escaner.escanear", cliente_id=cliente.id))

    if len(archivos) > MAX_ARCHIVOS_POR_LOTE:
        flash(f"Podés subir hasta {MAX_ARCHIVOS_POR_LOTE} facturas de una vez. Subí el resto en otra tanda.")
        return redirect(url_for("escaner.escanear", cliente_id=cliente.id))

    # Se recuerda para la próxima tanda (así no hay que volver a elegir
    # tipo/alícuota si son todas iguales).
    session["ultima_alicuota"] = alicuota
    session["ultimo_tipo"] = tipo

    items, fallidos, vistos = [], [], set()
    for archivo in archivos:
        extension = os.path.splitext(archivo.filename)[1].lower()
        if extension not in EXTENSIONES_PERMITIDAS:
            fallidos.append((archivo.filename, "No es una foto (JPG/PNG) ni un PDF."))
            continue

        path_temp = os.path.join(TMP_DIR, f"qr_{current_user.id}_{cliente.id}_{uuid.uuid4().hex}{extension}")
        archivo.save(path_temp)
        try:
            datos_qr = leer_qr_de_archivo(path_temp)
        except QrInvalido as e:
            fallidos.append((archivo.filename, str(e)))
            continue
        finally:
            if os.path.exists(path_temp):
                os.remove(path_temp)

        datos, advertencia = _datos_desde_qr(cliente, tipo, alicuota_pct, datos_qr)

        clave = (datos["tipo_comprobante"], datos["punto_venta"], datos["numero_comprobante"])
        if clave in vistos or _ya_cargado(cliente, datos):
            fallidos.append((archivo.filename, "Ese comprobante ya estaba cargado (o repetido en esta tanda); se salteó."))
            continue
        vistos.add(clave)

        datos["archivo"] = archivo.filename
        datos["advertencia"] = advertencia
        datos["error"] = None
        items.append(datos)

    if not items:
        for nombre, motivo in fallidos:
            flash(f"{nombre}: {motivo}")
        return redirect(url_for("escaner.escanear", cliente_id=cliente.id))

    return render_template("escanear_confirmar.html", cliente=cliente, items=items, fallidos=fallidos)


def _ya_cargado(cliente, datos) -> bool:
    return Comprobante.query.filter_by(
        cliente_id=cliente.id, tipo=datos["tipo"],
        tipo_comprobante=datos["tipo_comprobante"],
        punto_venta=int(datos["punto_venta"]), numero_comprobante=int(datos["numero_comprobante"]),
    ).first() is not None


def _datos_desde_qr(cliente, tipo, alicuota_pct, datos_qr):
    """Arma los datos a mostrar para un comprobante leído del QR. Devuelve (datos, advertencia)."""
    advertencia = None
    if tipo == "venta":
        # El emisor de la factura de venta debería ser el propio cliente.
        if "".join(filter(str.isdigit, datos_qr.cuit_emisor)) != "".join(
            filter(str.isdigit, cliente.cuit)
        ):
            advertencia = (
                f"El QR dice que el emisor es el CUIT {datos_qr.cuit_emisor}, pero "
                f"elegiste el cliente '{cliente.razon_social}' (CUIT {cliente.cuit}). "
                f"Revisá que sea el cliente correcto antes de guardar."
            )
        # La contraparte (quien compró) es el receptor del QR.
        if datos_qr.tipo_doc_receptor == TIPO_DOC_CUIT and datos_qr.cuit_receptor:
            cuit_contraparte = datos_qr.cuit_receptor
        else:
            cuit_contraparte = ""
    else:
        # En una compra, la contraparte (el proveedor) es el emisor del QR.
        cuit_contraparte = datos_qr.cuit_emisor

    # Cruce con el número impreso en el PDF: si no coincide con el del QR, se
    # usa el impreso (el QR a veces sale mal del sistema que emitió la factura)
    # y se avisa para que lo verifique.
    punto_venta, numero = datos_qr.punto_venta, datos_qr.numero_comprobante
    if datos_qr.numero_impreso is not None and (
        datos_qr.numero_impreso != datos_qr.numero_comprobante
        or (datos_qr.punto_venta_impreso is not None and datos_qr.punto_venta_impreso != datos_qr.punto_venta)
    ):
        punto_venta = datos_qr.punto_venta_impreso or datos_qr.punto_venta
        numero = datos_qr.numero_impreso
        aviso_numero = (
            f"El QR de esta factura dice {datos_qr.punto_venta:05d}-{datos_qr.numero_comprobante:08d}, pero el "
            f"PDF tiene impreso {punto_venta:05d}-{numero:08d}. Cargamos el impreso; verificalo antes de guardar."
        )
        advertencia = f"{advertencia} {aviso_numero}" if advertencia else aviso_numero

    razon_social_sugerida = (
        _buscar_razon_social_conocida(cuit_contraparte) if cuit_contraparte else None
    )

    try:
        periodo_sugerido = datetime.strptime(datos_qr.fecha, "%Y-%m-%d").strftime("%Y-%m")
    except ValueError:
        periodo_sugerido = ""

    # Lectura automática del PDF: neto, IVA y percepciones, verificados contra
    # el total del QR. Si no cierra exacto, no se propone nada (carga manual).
    detectado = detectar_percepciones(
        datos_qr.texto_pdf, datos_qr.importe_total, alicuota_pct, ALICUOTAS_DISPONIBLES
    )
    percepcion_iva = "0"
    filas_iibb = [{"jurisdiccion": "", "importe": ""}]
    extra = {"detectado": False, "neto_detectado": "", "iva_detectado": "", "percep_ref": ""}
    if detectado:
        if detectado.alicuota != alicuota_pct:
            aviso_alicuota = (
                f"Elegiste {alicuota_pct:g}% pero la factura cierra con {detectado.alicuota:g}%; "
                f"usamos {detectado.alicuota:g}%."
            )
            advertencia = f"{advertencia} {aviso_alicuota}" if advertencia else aviso_alicuota
            alicuota_pct = detectado.alicuota
        if detectado.percepcion_iva:
            percepcion_iva = f"{detectado.percepcion_iva:.2f}"
        if detectado.iibb:
            filas_iibb = [
                {"jurisdiccion": j if j != "Sin especificar" else "", "importe": f"{v:.2f}"}
                for j, v in detectado.iibb
            ]
        percep_total = round(detectado.percepcion_iva + sum(v for _, v in detectado.iibb), 2)
        extra = {
            "detectado": True,
            "neto_detectado": f"{detectado.neto:.2f}",
            "iva_detectado": f"{detectado.iva:.2f}",
            "percep_ref": f"{percep_total:.2f}",
            "diferencia": detectado.diferencia,
        }

    datos = {
        "tipo": tipo,
        "alicuota": alicuota_pct,
        "fecha": datos_qr.fecha,
        "periodo": periodo_sugerido,
        "tipo_comprobante": datos_qr.tipo_comprobante_texto,
        "punto_venta": punto_venta,
        "numero_comprobante": numero,
        "cuit": cuit_contraparte,
        "razon_social": razon_social_sugerida or "",
        "importe_total": datos_qr.importe_total,
        "percepcion_iva": percepcion_iva,
        "iibb": filas_iibb,
        **extra,
    }
    return datos, advertencia


def _filas_iibb_del_formulario(form, prefijo="") -> list[dict]:
    """
    Las percepciones de IIBB llegan como dos listas paralelas (una
    jurisdicción y un importe por fila, tantas filas como el usuario haya
    agregado con el botón "+"). Siempre devuelve al menos una fila (vacía)
    para que el formulario muestre un renglón donde escribir.
    """
    jurisdicciones = form.getlist(prefijo + "iibb_jurisdiccion")
    importes = form.getlist(prefijo + "iibb_importe")
    filas = [
        {"jurisdiccion": j.strip(), "importe": i.strip()}
        for j, i in zip_longest(jurisdicciones, importes, fillvalue="")
    ]
    return filas or [{"jurisdiccion": "", "importe": ""}]


def _percepciones_iibb_validas(filas: list[dict]) -> list[tuple[str, float]]:
    """
    Pasa las filas del formulario a [(jurisdicción, importe)], salteando las
    vacías. Lanza ValueError con un mensaje legible si alguna está mal.
    """
    resultado = []
    for fila in filas:
        jurisdiccion, importe_txt = fila["jurisdiccion"], fila["importe"]
        if not importe_txt and not jurisdiccion:
            continue  # renglón en blanco que quedó sin usar
        if not importe_txt:
            raise ValueError(f"Falta el importe de la percepción de IIBB de «{jurisdiccion}».")
        try:
            importe = round(float(importe_txt), 2)
        except ValueError:
            raise ValueError(f"El importe de IIBB «{importe_txt}» no es un número válido.")
        if importe < 0:
            raise ValueError("Las percepciones de IIBB no pueden ser negativas.")
        if importe == 0:
            continue
        resultado.append((jurisdiccion, importe))
    return resultado


def _datos_formulario(form, prefijo="") -> dict:
    def campo(nombre, defecto=""):
        return form.get(prefijo + nombre, defecto)

    return {
        "tipo": campo("tipo"),
        "alicuota": campo("alicuota"),
        "fecha": campo("fecha"),
        "periodo": campo("periodo").strip(),
        "tipo_comprobante": campo("tipo_comprobante").strip(),
        "punto_venta": campo("punto_venta"),
        "numero_comprobante": campo("numero_comprobante"),
        "cuit": campo("cuit").strip(),
        "razon_social": campo("razon_social").strip(),
        "importe_total": campo("importe_total"),
        "percepcion_iva": campo("percepcion_iva") or "0",
        "iibb": _filas_iibb_del_formulario(form, prefijo),
        "archivo": campo("archivo"),
        "advertencia": campo("advertencia") or None,
        "detectado": campo("detectado") == "1",
        "neto_detectado": campo("neto_detectado"),
        "iva_detectado": campo("iva_detectado"),
        "percep_ref": campo("percep_ref"),
        "error": None,
    }


def _guardar_comprobante(cliente, datos):
    """
    Valida y guarda un comprobante. Devuelve (estado, mensaje):
    "ok" | "duplicado" | "error" (con el mensaje a mostrar).
    """
    campos_requeridos = {
        "tipo": datos["tipo"] in ("venta", "compra"),
        "periodo": bool(datos["periodo"]),
        "cuit": bool(datos["cuit"]),
        "razon_social": bool(datos["razon_social"]),
    }
    if not all(campos_requeridos.values()):
        return "error", "Completá todos los campos (el CUIT y la razón social de la contraparte son obligatorios)."

    try:
        alicuota_pct = float(datos["alicuota"])
        importe_total = round(float(datos["importe_total"]), 2)
        percepcion_iva = round(float(datos["percepcion_iva"] or 0), 2)
    except (ValueError, TypeError):
        return "error", "Alguno de los importes no es un número válido."

    try:
        percepciones_iibb = _percepciones_iibb_validas(datos["iibb"])
    except ValueError as e:
        return "error", str(e)
    percepcion_iibb = round(sum(importe for _, importe in percepciones_iibb), 2)

    try:
        neto, iva = calcular_neto_e_iva(importe_total, alicuota_pct, percepcion_iva, percepcion_iibb)
    except PercepcionesSuperanElTotal as e:
        return "error", str(e)

    # Si las percepciones quedaron como las leyó el PDF, se guardan el neto y el
    # IVA tal cual están impresos (pueden diferir unos centavos de la fórmula).
    # Si el usuario tocó las percepciones, vale la fórmula.
    if datos.get("detectado") and datos.get("neto_detectado") and datos.get("iva_detectado"):
        try:
            neto_pdf = round(float(datos["neto_detectado"]), 2)
            iva_pdf = round(float(datos["iva_detectado"]), 2)
            percep_ref = round(float(datos["percep_ref"] or 0), 2)
            percep_actual = round(percepcion_iva + percepcion_iibb, 2)
            cierra = abs(neto_pdf + iva_pdf + percep_actual - importe_total) <= TOLERANCIA_REDONDEO + 0.005
            if abs(percep_actual - percep_ref) < 0.005 and cierra:
                neto, iva = neto_pdf, iva_pdf
        except ValueError:
            pass

    try:
        comprobante = Comprobante(
            cliente_id=cliente.id,
            tipo=datos["tipo"],
            periodo=datos["periodo"],
            fecha=datetime.strptime(datos["fecha"], "%Y-%m-%d").date(),
            tipo_comprobante=datos["tipo_comprobante"],
            punto_venta=int(datos["punto_venta"]),
            numero_comprobante=int(datos["numero_comprobante"]),
            cuit=datos["cuit"],
            razon_social=datos["razon_social"],
            neto_gravado=neto,
            iva=iva,
            percepcion_iva=percepcion_iva,
            percepcion_iibb=percepcion_iibb,
            origen="qr",
        )
        comprobante.percepciones_iibb = [
            PercepcionIibb(jurisdiccion=jurisdiccion or None, importe=importe)
            for jurisdiccion, importe in percepciones_iibb
        ]
    except (ValueError, TypeError) as e:
        return "error", f"Alguno de los datos no es válido: {e}"

    _memorizar_contraparte(datos["cuit"], datos["razon_social"])
    db.session.add(comprobante)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return "duplicado", (
            f"{comprobante.tipo_comprobante} {comprobante.punto_venta:05d}-"
            f"{comprobante.numero_comprobante:08d}"
        )
    return "ok", ""


@escaner_bp.route("/escanear/confirmar", methods=["POST"])
@estudio_required
def confirmar(cliente_id):
    """Guarda todas las facturas de la pantalla de revisión; las que tengan un error quedan para corregir."""
    cliente = _cliente_del_estudio_o_404(cliente_id)

    indices = request.form.getlist("indices")[:MAX_ARCHIVOS_POR_LOTE]
    guardados, duplicados, pendientes, periodos = 0, [], [], set()
    for indice in indices:
        datos = _datos_formulario(request.form, prefijo=f"f{indice}_")
        estado, mensaje = _guardar_comprobante(cliente, datos)
        if estado == "ok":
            guardados += 1
            periodos.add(datos["periodo"])
        elif estado == "duplicado":
            duplicados.append(mensaje)
        else:
            datos["error"] = mensaje
            pendientes.append(datos)

    partes = []
    if guardados:
        partes.append(f"{guardados} comprobante(s) cargado(s)")
    if duplicados:
        partes.append("ya estaban cargados: " + ", ".join(duplicados))
    if pendientes:
        partes.append(f"{len(pendientes)} necesitan que los corrijas")
    if partes:
        flash(". ".join(partes) + ".")

    if pendientes:
        return render_template("escanear_confirmar.html", cliente=cliente, items=pendientes, fallidos=[])

    periodo = periodos.pop() if len(periodos) == 1 else None
    return redirect(url_for("escaner.listado", cliente_id=cliente.id, periodo=periodo))


@escaner_bp.route("/comprobantes")
@estudio_required
def listado(cliente_id):
    cliente = _cliente_del_estudio_o_404(cliente_id)
    periodo = request.args.get("periodo", "").strip()

    query = Comprobante.query.filter_by(cliente_id=cliente.id)
    if periodo:
        query = query.filter_by(periodo=periodo)
    comprobantes = query.order_by(Comprobante.periodo.desc(), Comprobante.fecha.desc()).all()

    return render_template(
        "escanear_listado.html", cliente=cliente, comprobantes=comprobantes, periodo=periodo
    )


@escaner_bp.route("/comprobantes/exportar")
@estudio_required
def exportar(cliente_id):
    """
    Descarga un Excel con los comprobantes escaneados de UN solo tipo
    (?tipo=venta o ?tipo=compra), en una única hoja y con las mismas
    columnas que las plantillas de carga manual — así el archivo de ventas
    y el de compras se pueden subir tal cual en "Liquidar".

    La columna percepcion_iibb trae el TOTAL (suma de todas las
    jurisdicciones) y detalle_iibb el desglose por jurisdicción, solo para
    mirar (se ignora al subirlo).

    Si hay un período elegido en el listado, exporta solo ese período; si
    no, exporta todo lo que tenga cargado el cliente.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    cliente = _cliente_del_estudio_o_404(cliente_id)
    periodo = request.args.get("periodo", "").strip()
    tipo = request.args.get("tipo", "").strip()

    if tipo not in ("venta", "compra"):
        flash("Elegí si querés descargar las ventas o las compras.")
        return redirect(url_for("escaner.listado", cliente_id=cliente.id, periodo=periodo))
    etiqueta = "ventas" if tipo == "venta" else "compras"

    query = Comprobante.query.filter_by(cliente_id=cliente.id, tipo=tipo)
    if periodo:
        query = query.filter_by(periodo=periodo)
    comprobantes = query.order_by(Comprobante.fecha, Comprobante.id).all()

    if not comprobantes:
        flash(f"No hay {etiqueta} escaneadas para exportar{' en ' + periodo if periodo else ''}.")
        return redirect(url_for("escaner.listado", cliente_id=cliente.id, periodo=periodo))

    encabezados = [
        "fecha", "tipo_comprobante", "punto_venta", "numero_comprobante",
        "cuit", "razon_social", "neto_gravado", "iva",
        "percepcion_iva", "percepcion_iibb", "detalle_iibb",
    ]
    wb = Workbook()
    ws = wb.active
    ws.title = "Comprobantes"
    for col_idx, encabezado in enumerate(encabezados, start=1):
        celda = ws.cell(row=1, column=col_idx, value=encabezado)
        celda.font = Font(bold=True, color="FFFFFF")
        celda.fill = PatternFill("solid", fgColor="1F4E78")

    for fila, c in enumerate(comprobantes, start=2):
        valores = [
            c.fecha.strftime("%Y-%m-%d"), c.tipo_comprobante, c.punto_venta,
            c.numero_comprobante, c.cuit, c.razon_social,
            float(c.neto_gravado), float(c.iva),
            float(c.percepcion_iva or 0), float(c.percepcion_iibb or 0),
            c.detalle_iibb,
        ]
        for col_idx, valor in enumerate(valores, start=1):
            ws.cell(row=fila, column=col_idx, value=valor)

    anchos = [14, 20, 14, 20, 16, 30, 16, 14, 16, 16, 40]
    for col_idx, ancho in enumerate(anchos, start=1):
        ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = ancho

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    nombre = f"{cliente.razon_social.replace(' ', '_')}_{periodo or 'todos'}_{etiqueta}.xlsx"
    return send_file(
        buffer, as_attachment=True, download_name=nombre,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@escaner_bp.route("/comprobantes/<int:comprobante_id>/eliminar", methods=["POST"])
@estudio_required
def eliminar(cliente_id, comprobante_id):
    cliente = _cliente_del_estudio_o_404(cliente_id)
    comprobante = Comprobante.query.filter_by(id=comprobante_id, cliente_id=cliente.id).first()
    if comprobante is None:
        flash("Ese comprobante no existe.")
    else:
        periodo = comprobante.periodo
        db.session.delete(comprobante)
        db.session.commit()
        flash("Comprobante eliminado.")
        return redirect(url_for("escaner.listado", cliente_id=cliente.id, periodo=periodo))
    return redirect(url_for("escaner.listado", cliente_id=cliente.id))
