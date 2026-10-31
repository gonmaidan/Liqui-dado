"""
clientes.py
-------------
CRUD simple de clientes (contribuyentes) para el estudio logueado.

Todas las consultas filtran SIEMPRE por estudio_id == current_user.id.
Nunca se confía en un id de cliente que venga por la URL sin validar que
pertenezca al estudio logueado — así un estudio no puede, ni por error de
tipeo en la URL, llegar a ver un cliente de otro estudio.
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import current_user

from extensions import db
from models import Cliente, ClienteObligacion
from obligaciones import TIPOS_PERSONA, OBLIGACIONES, obligaciones_para, claves_validas
from auth import estudio_required

clientes_bp = Blueprint("clientes", __name__, url_prefix="/clientes")


def _cliente_del_estudio_o_404(cliente_id: int) -> Cliente:
    """Devuelve el cliente solo si pertenece al estudio logueado; si no, 404."""
    cliente = db.session.get(Cliente, cliente_id)
    if cliente is None or cliente.estudio_id != current_user.id:
        abort(404)
    return cliente


@clientes_bp.route("/")
@estudio_required
def listar():
    clientes = (
        Cliente.query.filter_by(estudio_id=current_user.id)
        .order_by(Cliente.razon_social)
        .all()
    )
    return render_template(
        "clientes.html", clientes=clientes, tipos_persona=TIPOS_PERSONA, obligaciones=OBLIGACIONES,
    )


def _leer_tipo_y_obligaciones(form):
    tipo = form.get("tipo_persona", "fisica")
    if tipo not in TIPOS_PERSONA:
        tipo = "fisica"
    return tipo, claves_validas(tipo, form.getlist("obligaciones"))


@clientes_bp.route("/nuevo", methods=["POST"])
@estudio_required
def crear():
    cuit = request.form.get("cuit", "").strip()
    razon_social = request.form.get("razon_social", "").strip()

    if not cuit or not razon_social:
        flash("Completá CUIT y razón social.")
        return redirect(url_for("clientes.listar"))

    ya_existe = Cliente.query.filter_by(estudio_id=current_user.id, cuit=cuit).first()
    if ya_existe:
        flash(f"Ya tenés cargado un cliente con CUIT {cuit} ({ya_existe.razon_social}).")
        return redirect(url_for("clientes.listar"))

    tipo, claves = _leer_tipo_y_obligaciones(request.form)
    cliente = Cliente(estudio_id=current_user.id, cuit=cuit, razon_social=razon_social, tipo_persona=tipo)
    cliente.obligaciones = [ClienteObligacion(obligacion=k) for k in claves]
    db.session.add(cliente)
    db.session.commit()
    if claves:
        flash(f"Cliente '{razon_social}' agregado. Ya podés ver sus vencimientos.")
    else:
        flash(f"Cliente '{razon_social}' agregado. No tildaste obligaciones: sin eso no se le muestran vencimientos.")
    return redirect(url_for("clientes.listar"))


@clientes_bp.route("/<int:cliente_id>/eliminar", methods=["POST"])
@estudio_required
def eliminar(cliente_id):
    cliente = _cliente_del_estudio_o_404(cliente_id)
    nombre = cliente.razon_social
    db.session.delete(cliente)
    db.session.commit()
    flash(f"Cliente '{nombre}' eliminado (junto con su historial de liquidaciones).")
    return redirect(url_for("clientes.listar"))


@clientes_bp.route("/<int:cliente_id>/obligaciones", methods=["GET", "POST"])
@estudio_required
def obligaciones_cliente(cliente_id):
    """Cambiar el tipo de persona y las obligaciones de un cliente ya cargado."""
    cliente = _cliente_del_estudio_o_404(cliente_id)
    if request.method == "POST":
        tipo, claves = _leer_tipo_y_obligaciones(request.form)
        cliente.tipo_persona = tipo
        # Se modifica solo la diferencia: reemplazar la lista entera hace que SQLAlchemy
        # inserte las nuevas antes de borrar las viejas y choque con la restricción de unicidad.
        nuevas = set(claves)
        for existente in list(cliente.obligaciones):
            if existente.obligacion not in nuevas:
                cliente.obligaciones.remove(existente)
        for k in sorted(nuevas - cliente.claves_obligaciones):
            cliente.obligaciones.append(ClienteObligacion(obligacion=k))
        db.session.commit()
        flash(f"Obligaciones de '{cliente.razon_social}' actualizadas.")
        return redirect(url_for("vencimientos.listado", cliente_id=cliente.id))
    return render_template(
        "cliente_obligaciones.html", cliente=cliente,
        tipos_persona=TIPOS_PERSONA, obligaciones=OBLIGACIONES,
    )
