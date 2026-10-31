"""
qr_scanner.py
----------------
Lee el código QR de AFIP de una foto o PDF de una factura electrónica, y
devuelve los datos ya decodificados: fecha, CUIT emisor, tipo y número de
comprobante, punto de venta, e importe total.

IMPORTANTE — lo que el QR de AFIP no trae:
  - El desglose entre neto gravado e IVA: solo trae el importe TOTAL
    (que puede incluir percepciones). Por eso el usuario elige de
    antemano la alícuota de la factura (ver calcular_neto_e_iva), y si
    hay percepciones las escribe a mano — primero se las resta al total,
    y sobre lo que queda se aplica la alícuota. Da exacto siempre que la
    factura tenga una sola alícuota de IVA.
  - La razón social de la contraparte: solo viene el CUIT. La razón
    social se pide una vez y se memoriza (ver ContraparteConocida en
    models.py / escaner.py).
"""

import base64
import json
import re
from urllib.parse import unquote
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

# Tabla de "Tipos de comprobante" de AFIP (la misma que usa el código QR en
# el campo "tipoCmp"). Mapea a los mismos textos que ya reconoce
# afip_libro_iva.py, para que un comprobante escaneado se trate igual que
# uno cargado a mano por Excel.
CODIGO_A_TIPO_COMPROBANTE = {
    1: "Factura A",
    2: "Nota de Débito A",
    3: "Nota de Crédito A",
    6: "Factura B",
    7: "Nota de Débito B",
    8: "Nota de Crédito B",
    11: "Factura C",
    12: "Nota de Débito C",
    13: "Nota de Crédito C",
}

MAX_PAGINAS_QR = 3  # páginas del PDF donde se busca el QR

TIPO_DOC_CUIT = 80  # código AFIP para "CUIT" en tipoDocRec


class QrInvalido(Exception):
    """El archivo no tiene un QR legible, o no es un QR de factura de AFIP."""


@dataclass
class DatosQrAfip:
    fecha: str  # "AAAA-MM-DD"
    cuit_emisor: str
    tipo_comprobante_codigo: int
    tipo_comprobante_texto: str
    punto_venta: int
    numero_comprobante: int
    importe_total: float
    tipo_doc_receptor: int
    cuit_receptor: Optional[str]
    # Número impreso en el PDF (solo si el archivo es un PDF con texto): sirve
    # para cruzarlo contra lo que dice el QR y avisar si no coinciden.
    punto_venta_impreso: Optional[int] = None
    numero_impreso: Optional[int] = None
    # Texto del PDF (primeras páginas), para leer neto / IVA / percepciones.
    texto_pdf: str = ""


def _a_gris_bgr(datos: np.ndarray, n: int) -> np.ndarray:
    if n == 4:
        return cv2.cvtColor(datos, cv2.COLOR_RGBA2GRAY)
    if n == 3:
        return cv2.cvtColor(datos, cv2.COLOR_RGB2GRAY)
    return datos.reshape(datos.shape[0], datos.shape[1])


def _imagenes_candidatas(path: str):
    """
    Genera, de a una (para no gastar memoria), las imágenes donde puede estar el QR.
    Para una foto: la foto. Para un PDF: primero las imágenes incrustadas (muchos
    sistemas pegan el QR como imagen chica), después las primeras páginas
    renderizadas a distintas resoluciones, en escala de grises.
    """
    if not path.lower().endswith(".pdf"):
        datos = cv2.imread(path)
        if datos is None:
            raise QrInvalido(
                "No pude abrir el archivo como imagen. Probá con una foto en formato "
                "JPG o PNG, o un PDF con la factura."
            )
        yield datos
        return

    import pymupdf  # PyMuPDF (el paquete se importa como "pymupdf"; "fitz" es el nombre viejo)

    doc = pymupdf.open(path)
    if doc.page_count == 0:
        raise QrInvalido("El PDF no tiene páginas.")
    paginas = range(min(doc.page_count, MAX_PAGINAS_QR))

    # 1) QR pegado como imagen: se lo agranda y se le pone borde blanco (la "zona de silencio").
    for nro in paginas:
        for info in doc[nro].get_images(full=True):
            try:
                pix = pymupdf.Pixmap(doc, info[0])
                if pix.colorspace is not None and pix.colorspace.n > 3:
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                if pix.alpha:
                    pix = pymupdf.Pixmap(pix, 0)
                if max(pix.width, pix.height) < 60 or max(pix.width, pix.height) > 4000:
                    continue
                gris = _a_gris_bgr(
                    np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n), pix.n
                )
                if min(gris.shape) < 400:
                    factor = int(np.ceil(400 / min(gris.shape)))
                    gris = cv2.resize(gris, None, fx=factor, fy=factor, interpolation=cv2.INTER_NEAREST)
                yield cv2.copyMakeBorder(gris, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)
            except Exception:
                continue

    # 2) Páginas renderizadas: de menor a mayor resolución.
    for dpi in (200, 300, 400):
        for nro in paginas:
            pix = doc[nro].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
            yield np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)


def _decodificar_qr(imagen: np.ndarray) -> Optional[str]:
    """Texto del QR de la imagen, o None. Prueba también achicando fotos muy grandes."""
    detector = cv2.QRCodeDetector()
    texto, _, _ = detector.detectAndDecode(imagen)
    if not texto:
        alto, ancho = imagen.shape[:2]
        if max(alto, ancho) > 1600:
            escala = 1600 / max(alto, ancho)
            texto, _, _ = detector.detectAndDecode(cv2.resize(imagen, None, fx=escala, fy=escala))
    return texto or None


def _parsear_url_afip(url: str) -> dict:
    """
    La URL es https://www.afip.gob.ar/fe/qr/?p=<base64 de un JSON>. Algunos
    sistemas omiten el "p=" y ponen el base64 directo después del "?".
    """
    if "afip.gob.ar" not in url.lower() and "arca.gob.ar" not in url.lower():
        raise QrInvalido(
            "El QR que encontré no parece ser el de una factura electrónica de AFIP."
        )
    consulta = url.split("?", 1)[1] if "?" in url else ""
    parametro = consulta.split("&", 1)[0]
    if parametro.startswith("p="):
        parametro = parametro[2:]
    parametro = unquote(parametro).strip()
    if not parametro:
        raise QrInvalido("El QR no trae datos de factura de AFIP.")
    try:
        relleno = "=" * (-len(parametro) % 4)
        datos = json.loads(base64.urlsafe_b64decode(parametro + relleno))
        if not isinstance(datos, dict):
            raise ValueError("no es un objeto JSON")
    except Exception as e:
        raise QrInvalido(f"No pude leer los datos del QR de AFIP: {e}")
    return datos


def _url_en_texto(texto: str) -> Optional[str]:
    """Algunos PDFs traen la URL del QR como texto; si está, no hace falta ni mirar la imagen."""
    m = re.search(r"https?://\S*(?:afip|arca)\.gob\.ar/fe/qr/?\?\S+", texto, re.IGNORECASE)
    return m.group(0) if m else None


def _buscar_datos_qr(path: str, texto_pdf: str) -> dict:
    url = _url_en_texto(texto_pdf) if texto_pdf else None
    if url:
        try:
            return _parsear_url_afip(url)
        except QrInvalido:
            pass
    vio_qr_ajeno = False
    for imagen in _imagenes_candidatas(path):
        texto = _decodificar_qr(imagen)
        if not texto:
            continue
        try:
            return _parsear_url_afip(texto)
        except QrInvalido:
            vio_qr_ajeno = True  # un QR que no es de AFIP (ej. de pago): sigo buscando
    if vio_qr_ajeno:
        raise QrInvalido("El archivo tiene un QR, pero no es el de una factura electrónica de AFIP.")
    raise QrInvalido(
        "No encontré ningún código QR legible en el archivo. Probá con una "
        "foto más nítida, bien enfocada, con buena luz y el QR completo "
        "dentro de la imagen."
    )


def texto_de_pdf(path: str) -> str:
    """Texto de las primeras páginas de un PDF ("" si no es PDF o es una imagen escaneada)."""
    if not path.lower().endswith(".pdf"):
        return ""
    try:
        import pymupdf

        doc = pymupdf.open(path)
        return "\n".join(doc[i].get_text() for i in range(min(doc.page_count, MAX_PAGINAS_QR)))
    except Exception:
        return ""


def _numero_impreso_en_pdf(path: str, texto_pdf: Optional[str] = None):
    """
    Si es un PDF con texto, busca el punto de venta y el número impresos
    ("Punto de Venta: 01111", "Comp. Nro: 00024794", o "1111-00024794").
    Devuelve (punto_venta, numero) o (None, None) si no los encuentra.
    """
    texto = texto_pdf if texto_pdf is not None else texto_de_pdf(path)
    texto = texto.split("\f")[0] if texto else ""
    if not texto:
        return None, None

    pv = re.search(r"Punto\s+de\s+Venta\s*:?\s*(\d{1,5})", texto, re.IGNORECASE)
    nro = re.search(r"Comp\.?\s*Nro\.?\s*:?\s*(\d{1,8})", texto, re.IGNORECASE)
    if pv and nro:
        return int(pv.group(1)), int(nro.group(1))
    junto = re.search(r"\b(\d{4,5})\s*-\s*(\d{8})\b", texto)
    if junto:
        return int(junto.group(1)), int(junto.group(2))
    return None, None


def leer_qr_de_archivo(path: str) -> DatosQrAfip:
    """Punto de entrada: le pasás la ruta de la foto/PDF y te devuelve los datos ya decodificados."""
    texto_pdf = texto_de_pdf(path)
    datos = _buscar_datos_qr(path, texto_pdf)
    resultado = _datos_qr_desde_dict(datos)
    resultado.texto_pdf = texto_pdf
    resultado.punto_venta_impreso, resultado.numero_impreso = _numero_impreso_en_pdf(path, texto_pdf)
    return resultado


def _datos_qr_desde_dict(datos: dict) -> DatosQrAfip:
    try:
        tipo_codigo = int(datos["tipoCmp"])
        tipo_doc_receptor = int(datos.get("tipoDocRec", 0) or 0)
        cuit_receptor = str(datos.get("nroDocRec") or "").strip() or None
        return DatosQrAfip(
            fecha=str(datos["fecha"]),
            cuit_emisor=str(datos["cuit"]).strip(),
            tipo_comprobante_codigo=tipo_codigo,
            tipo_comprobante_texto=CODIGO_A_TIPO_COMPROBANTE.get(
                tipo_codigo, f"Tipo {tipo_codigo} (no reconocido, revisar a mano)"
            ),
            punto_venta=int(datos["ptoVta"]),
            numero_comprobante=int(datos["nroCmp"]),
            importe_total=float(datos["importe"]),
            tipo_doc_receptor=tipo_doc_receptor,
            cuit_receptor=cuit_receptor,
        )
    except KeyError as e:
        raise QrInvalido(
            f"Al QR le falta un dato esperado ({e}). ¿Seguro que es una factura "
            f"electrónica de AFIP (no un presupuesto o remito)?"
        )


class PercepcionesSuperanElTotal(Exception):
    """Las percepciones tipeadas son mayores (o iguales) al importe total del QR."""


def calcular_neto_e_iva(importe_total: float, alicuota_pct: float, percepcion_iva: float = 0.0, percepcion_iibb: float = 0.0):
    """
    El QR de AFIP solo trae el importe TOTAL (neto + IVA + percepciones,
    todo junto, sin discriminar). Para obtener neto gravado e IVA:

      1. Se restan las percepciones del importe total (si no hay
         percepciones, esto no cambia nada).
      2. Sobre lo que queda, se aplica la alícuota elegida para esa
         factura (21%, 10.5%, etc.) y se calcula neto e IVA "hacia atrás".

    Da exacto siempre que la factura tenga una única alícuota de IVA —
    que es el caso más común para un contribuyente chico.
    """
    base = round(importe_total - percepcion_iva - percepcion_iibb, 2)
    if base <= 0:
        raise PercepcionesSuperanElTotal(
            f"Las percepciones (${percepcion_iva + percepcion_iibb:,.2f}) son mayores o "
            f"iguales al importe total de la factura (${importe_total:,.2f}). Revisá los "
            f"montos."
        )
    neto = round(base / (1 + alicuota_pct / 100), 2)
    iva = round(base - neto, 2)
    return neto, iva
