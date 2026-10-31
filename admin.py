"""
admin.py
----------
Panel del dueño de la plataforma (Admin). A propósito, este blueprint
NUNCA importa ni consulta el modelo Cliente más allá de un conteo
(func.count) — el admin gestiona cuentas de estudios, no ve CUITs,
razones sociales ni nada de la liquidación de ningún cliente.
"""

import secrets
import string

from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash
from sqlalchemy import func

from extensions import db
from models import Estudio, Cliente, Vencimiento
from obligaciones import OBLIGACIONES, NOMBRES, detectar_obligacion
from auth import admin_required

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _generar_password_temporal(largo: int = 12) -> str:
    alfabeto = string.ascii_letters + string.digits
    return "".join(secrets.choice(alfabeto) for _ in range(largo))


@admin_bp.route("/")
@admin_required
def panel():
    # Un solo query: estudio + cantidad de clientes (sin traer los clientes en sí).
    filas = (
        db.session.query(Estudio, func.count(Cliente.id))
        .outerjoin(Cliente, Cliente.estudio_id == Estudio.id)
        .group_by(Estudio.id)
        .order_by(Estudio.fecha_alta.desc())
        .all()
    )
    return render_template("admin_dashboard.html", filas=filas)


@admin_bp.route("/estudios/nuevo", methods=["POST"])
@admin_required
def crear_estudio():
    nombre = request.form.get("nombre", "").strip()
    email = request.form.get("email", "").strip().lower()

    if not nombre or not email:
        flash("Completá nombre y email del estudio.")
        return redirect(url_for("admin.panel"))

    if Estudio.query.filter_by(email=email).first():
        flash(f"Ya existe un estudio con el email {email}.")
        return redirect(url_for("admin.panel"))

    password_temporal = _generar_password_temporal()
    estudio = Estudio(nombre=nombre, email=email)
    estudio.set_password(password_temporal)
    db.session.add(estudio)
    db.session.commit()

    flash(
        f"Estudio '{nombre}' creado. Contraseña temporal: {password_temporal} "
        f"— pasásela por un canal seguro, no queda guardada en ningún lado."
    )
    return redirect(url_for("admin.panel"))


@admin_bp.route("/estudios/<int:estudio_id>/toggle", methods=["POST"])
@admin_required
def alternar_estudio(estudio_id):
    estudio = db.session.get(Estudio, estudio_id)
    if estudio is None:
        flash("Ese estudio no existe.")
        return redirect(url_for("admin.panel"))

    estudio.activo = not estudio.activo
    db.session.commit()
    estado = "activado" if estudio.activo else "suspendido"
    flash(f"Estudio '{estudio.nombre}' {estado}.")
    return redirect(url_for("admin.panel"))


# ---------------------------------------------------------------------------
# Calendario de vencimientos (base común a todos los estudios)
# ---------------------------------------------------------------------------

def _parsear_fecha(texto: str):
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(texto.strip(), formato).date()
        except ValueError:
            continue
    return None


def _parsear_terminaciones(texto: str):
    """'0,1' -> '01'; '' / 'todas' -> ''; devuelve None si algo no es un dígito."""
    texto = texto.strip().lower()
    if texto in ("", "todas", "todos", "*"):
        return ""
    digitos = [p.strip() for p in texto.replace(" ", ",").replace(";", ",").split(",") if p.strip()]
    if not all(len(d) == 1 and d.isdigit() for d in digitos):
        return None
    return "".join(sorted(set(digitos)))


@admin_bp.route("/vencimientos")
@admin_required
def vencimientos():
    filas = Vencimiento.query.order_by(Vencimiento.fecha, Vencimiento.impuesto).all()
    return render_template(
        "admin_vencimientos.html", filas=filas, obligaciones=OBLIGACIONES, nombres=NOMBRES,
        sin_clasificar=sum(1 for f in filas if f.obligacion is None),
    )


@admin_bp.route("/vencimientos/importar", methods=["POST"])
@admin_required
def importar_vencimientos():
    """Una línea por vencimiento: impuesto ; fecha ; terminaciones."""
    cargados, repetidos, errores = 0, 0, []
    for nro, linea in enumerate(request.form.get("texto", "").splitlines(), start=1):
        if not linea.strip():
            continue
        partes = [p.strip() for p in linea.split(";")]
        if len(partes) < 2 or not partes[0]:
            errores.append(f"línea {nro}")
            continue
        fecha = _parsear_fecha(partes[1])
        terminaciones = _parsear_terminaciones(partes[2] if len(partes) > 2 else "")
        if fecha is None or terminaciones is None:
            errores.append(f"línea {nro}")
            continue
        existe = Vencimiento.query.filter_by(
            impuesto=partes[0][:120], fecha=fecha, terminaciones=terminaciones
        ).first()
        if existe:
            repetidos += 1
            continue
        db.session.add(Vencimiento(
            impuesto=partes[0][:120], fecha=fecha, terminaciones=terminaciones,
            obligacion=detectar_obligacion(partes[0]),
        ))
        cargados += 1
    db.session.commit()

    msg = f"Vencimientos cargados: {cargados}."
    if repetidos:
        msg += f" Ya existían: {repetidos}."
    if errores:
        msg += " No se pudieron leer: " + ", ".join(errores[:10]) + "."
    flash(msg)
    return redirect(url_for("admin.vencimientos"))


@admin_bp.route("/vencimientos/<int:vencimiento_id>/eliminar", methods=["POST"])
@admin_required
def eliminar_vencimiento(vencimiento_id):
    v = db.session.get(Vencimiento, vencimiento_id)
    if v is not None:
        db.session.delete(v)
        db.session.commit()
        flash("Vencimiento eliminado.")
    return redirect(url_for("admin.vencimientos"))


@admin_bp.route("/vencimientos/<int:vencimiento_id>/obligacion", methods=["POST"])
@admin_required
def asignar_obligacion(vencimiento_id):
    v = db.session.get(Vencimiento, vencimiento_id)
    clave = request.form.get("obligacion", "")
    if v is not None and (clave in NOMBRES or clave == ""):
        v.obligacion = clave or None
        db.session.commit()
        flash("Obligación actualizada.")
    return redirect(url_for("admin.vencimientos"))
