"""
sifere_txt.py
---------------
TXT de importación de percepciones de IIBB para SIFERE web (Deducciones → Percepciones → Importar).
Largo fijo, sin separadores, un renglón por percepción (51 caracteres):

    jurisdicción(3) CUIT(13, con guiones) fecha(10, dd/mm/aaaa) punto de venta(4) comprobante(8)
    tipo y letra(2) importe(11, con coma decimal y ceros a la izquierda)

Ejemplo (el que dio el estudio, de SIFERE):
    91130-68178305-530/09/2026011800732809FA00000062,63

Los códigos de jurisdicción siguen la numeración del Convenio Multilateral (901 a 924).
"""

CODIGOS_JURISDICCION = {
    "CABA": 901, "Buenos Aires": 902, "Catamarca": 903, "Córdoba": 904, "Corrientes": 905, "Chaco": 906,
    "Chubut": 907, "Entre Ríos": 908, "Formosa": 909, "Jujuy": 910, "La Pampa": 911, "La Rioja": 912,
    "Mendoza": 913, "Misiones": 914, "Neuquén": 915, "Río Negro": 916, "Salta": 917, "San Juan": 918,
    "San Luis": 919, "Santa Cruz": 920, "Santa Fe": 921, "Santiago del Estero": 922,
    "Tierra del Fuego": 923, "Tucumán": 924,
}
LARGO_RENGLON = 51


def tipo_y_letra(tipo_comprobante: str):
    """'Factura A' -> 'FA'. Solo facturas: las notas de crédito/débito devuelven None (ver generar_txt)."""
    t = (tipo_comprobante or "").strip().lower().replace("é", "e")
    partes = t.split()
    if len(partes) == 2 and partes[0] == "factura" and partes[1] in ("a", "b", "c", "e", "m"):
        return "F" + partes[1].upper()
    return None


def cuit_con_guiones(cuit: str):
    d = "".join(c for c in str(cuit or "") if c.isdigit())
    return f"{d[:2]}-{d[2:10]}-{d[10]}" if len(d) == 11 else None


def renglon(jurisdiccion, cuit, fecha, punto_venta, numero, tipo_comprobante, importe):
    """Arma un renglón o lanza ValueError con el motivo."""
    codigo = CODIGOS_JURISDICCION.get(jurisdiccion)
    if codigo is None:
        raise ValueError(f"jurisdicción «{jurisdiccion}» sin código")
    cuit_f = cuit_con_guiones(cuit)
    if cuit_f is None:
        raise ValueError(f"CUIT del proveedor inválido ({cuit})")
    tl = tipo_y_letra(tipo_comprobante)
    if tl is None:
        raise ValueError(f"tipo de comprobante «{tipo_comprobante}» no soportado (solo facturas)")
    if not (0 <= int(punto_venta) <= 9999) or not (0 <= int(numero) <= 99999999):
        raise ValueError(f"punto de venta {punto_venta} / número {numero} no entran en 4 y 8 posiciones")
    if importe <= 0:
        raise ValueError("importe no positivo")
    imp = f"{importe:.2f}".replace(".", ",").zfill(11)
    if len(imp) > 11:
        raise ValueError("importe demasiado grande")
    linea = f"{codigo}{cuit_f}{fecha:%d/%m/%Y}{int(punto_venta):04d}{int(numero):08d}{tl}{imp}"
    assert len(linea) == LARGO_RENGLON, linea
    return linea


def generar_txt(filas):
    """
    filas: dicts de cm._filas_percepciones. Devuelve (texto, omitidas) donde omitidas es la lista de
    (descripcion, motivo) de lo que no se pudo incluir. Solo van las percepciones de jurisdicciones
    del Convenio del cliente.
    """
    lineas, omitidas = [], []
    for f in filas:
        desc = f"{f['comprobante']} {f['punto_venta']:04d}-{f['numero']:08d} ({f['razon_social']}) — {f['jurisdiccion'] or f['texto_original'] or 'sin jurisdicción'}"
        if not f["jurisdiccion"]:
            omitidas.append((desc, "no se pudo identificar la jurisdicción"))
            continue
        if not f["en_convenio"]:
            omitidas.append((desc, "la jurisdicción no está entre las del Convenio del cliente"))
            continue
        try:
            lineas.append(renglon(f["jurisdiccion"], f["cuit_agente"], f["fecha"], f["punto_venta"], f["numero"], f["comprobante"], f["importe"]))
        except ValueError as e:
            omitidas.append((desc, str(e)))
    return "".join(l + "\r\n" for l in lineas), omitidas
