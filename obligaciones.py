"""
obligaciones.py
-----------------
Catálogo de obligaciones (impuestos/tareas) que un estudio puede tildar para
cada cliente. Cada vencimiento del calendario del Admin se asocia a una de
estas obligaciones; así a cada cliente se le muestran solo los vencimientos
de lo que realmente trabaja el estudio.

Cada obligación dice a qué tipo de persona aplica: "fisica", "juridica" o
"ambas". Eso solo sirve para ofrecer las opciones que corresponden.
"""

import re
import unicodedata

TIPOS_PERSONA = {"fisica": "Persona física", "juridica": "Persona jurídica"}

# (clave, nombre, aplica_a)
OBLIGACIONES = [
    ("iva", "IVA", "ambas"),
    ("autonomos", "Autónomos", "fisica"),
    ("monotributo", "Monotributo", "fisica"),
    ("f931", "F.931 - Cargas sociales (empleador)", "ambas"),
    ("ganancias", "Ganancias", "ambas"),
    ("bienes_personales", "Bienes Personales", "fisica"),
    ("iibb_caba", "IIBB CABA (AGIP)", "ambas"),
    ("iibb_arba", "IIBB Buenos Aires (ARBA)", "ambas"),
    ("convenio_multilateral", "Convenio Multilateral (CM03/CM04)", "ambas"),
]

NOMBRES = {clave: nombre for clave, nombre, _ in OBLIGACIONES}


def obligaciones_para(tipo_persona: str):
    """Obligaciones que se pueden ofrecer para ese tipo de persona."""
    return [
        (clave, nombre) for clave, nombre, aplica in OBLIGACIONES
        if aplica in ("ambas", tipo_persona)
    ]


def claves_validas(tipo_persona: str, claves) -> list[str]:
    """Se queda solo con las claves que existen y corresponden al tipo de persona."""
    permitidas = {c for c, _ in obligaciones_para(tipo_persona)}
    return [c for c in dict.fromkeys(claves) if c in permitidas]


def _normalizar(texto: str) -> str:
    sin_acentos = unicodedata.normalize("NFD", texto or "")
    return "".join(ch for ch in sin_acentos if unicodedata.category(ch) != "Mn").lower()


def detectar_obligacion(impuesto: str):
    """Adivina la obligación a partir del nombre del vencimiento; None si no la reconoce."""
    t = _normalizar(impuesto)
    if "convenio" in t or "comarb" in t or re.search(r"\bcm0?[34]\b", t):
        return "convenio_multilateral"
    if "caba" in t or "agip" in t:
        return "iibb_caba"
    if "arba" in t or "buenos aires" in t:
        return "iibb_arba"
    if "bienes personales" in t:
        return "bienes_personales"
    if "monotributo" in t:
        return "monotributo"
    if "autonomo" in t:
        return "autonomos"
    if "f.931" in t or "f931" in t or "cargas sociales" in t:
        return "f931"
    if "ganancias" in t:
        return "ganancias"
    if re.search(r"\biva\b", t):
        return "iva"
    return None
