"""
vencimientos.py
-----------------
"Próximos vencimientos" de cada cliente, según la terminación de su CUIT.

El calendario base lo carga el Admin (ver admin.py). Cada estudio puede
cambiar la fecha de un vencimiento si ARCA lo prorrogó: el cambio es solo
del estudio (VencimientoAjuste) y vale para todos sus clientes a los que
les aplica ese vencimiento. El calendario base no se toca.
"""

from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import current_user

from extensions import db
from models import Vencimiento, VencimientoAjuste
from auth import estudio_required
from clientes import _cliente_del_estudio_o_404

vencimientos_bp = Blueprint("vencimientos", __name__, url_prefix="/clientes/<int:cliente_id>/vencimientos")

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def hoy_argentina() -> date:
    return datetime.now(timezone(timedelta(hours=-3))).date()


def _mes_valido(texto: str):
    try:
        return datetime.strptime(texto, "%Y-%m")
    except (ValueError, TypeError):
        return None


@vencimientos_bp.route("/")
@estudio_required
def listado(cliente_id):
    cliente = _cliente_del_estudio_o_404(cliente_id)
    hoy = hoy_argentina()

    mes = request.args.get("mes", "").strip()
    mes_dt = _mes_valido(mes)
    if mes and mes_dt is None:
        flash("El mes tiene que ser AAAA-MM (por ejemplo 2026-11).")
        mes = ""

    ajustes = {
        a.vencimiento_id: a
        for a in VencimientoAjuste.query.filter_by(estudio_id=current_user.id).all()
    }

    tildadas = cliente.claves_obligaciones
    filas = []
    for v in Vencimiento.query.all():
        if v.obligacion not in tildadas or not v.aplica_a(cliente.cuit):
            continue
        ajuste = ajustes.get(v.id)
        efectiva = ajuste.fecha_nueva if ajuste else v.fecha
        if mes_dt is not None:
            if (efectiva.year, efectiva.month) != (mes_dt.year, mes_dt.month):
                continue
        elif efectiva < hoy:
            continue  # sin filtro de mes: solo los que todavía no vencieron
        filas.append({
            "v": v,
            "ajuste": ajuste,
            "fecha": efectiva,
            "dias": (efectiva - hoy).days,
            "dia_semana": DIAS[efectiva.weekday()],
        })
    filas.sort(key=lambda f: (f["fecha"], f["v"].impuesto))

    return render_template(
        "vencimientos.html", cliente=cliente, filas=filas, mes=mes,
        base_vacia=Vencimiento.query.first() is None,
        sin_obligaciones=not tildadas,
    )


@vencimientos_bp.route("/<int:vencimiento_id>/ajustar", methods=["POST"])
@estudio_required
def ajustar(cliente_id, vencimiento_id):
    _cliente_del_estudio_o_404(cliente_id)
    mes = request.form.get("mes", "")
    destino = redirect(url_for("vencimientos.listado", cliente_id=cliente_id, mes=mes or None))

    v = db.session.get(Vencimiento, vencimiento_id)
    if v is None:
        flash("Ese vencimiento ya no existe.")
        return destino
    try:
        fecha_nueva = datetime.strptime(request.form.get("fecha_nueva", ""), "%Y-%m-%d").date()
    except ValueError:
        flash("La fecha nueva no es válida.")
        return destino

    nota = request.form.get("nota", "").strip()[:200] or None
    ajuste = VencimientoAjuste.query.filter_by(
        estudio_id=current_user.id, vencimiento_id=v.id
    ).first()
    if fecha_nueva == v.fecha and ajuste is None:
        return destino  # sin cambios reales
    if ajuste is None:
        ajuste = VencimientoAjuste(estudio_id=current_user.id, vencimiento_id=v.id)
        db.session.add(ajuste)
    ajuste.fecha_nueva = fecha_nueva
    ajuste.nota = nota
    db.session.commit()
    flash(
        f"{v.impuesto}: fecha cambiada a {fecha_nueva.strftime('%d/%m/%Y')}. "
        "Vale para todos tus clientes a los que les aplica este vencimiento."
    )
    return destino


@vencimientos_bp.route("/<int:vencimiento_id>/restaurar", methods=["POST"])
@estudio_required
def restaurar(cliente_id, vencimiento_id):
    _cliente_del_estudio_o_404(cliente_id)
    mes = request.form.get("mes", "")
    ajuste = VencimientoAjuste.query.filter_by(
        estudio_id=current_user.id, vencimiento_id=vencimiento_id
    ).first()
    if ajuste is not None:
        db.session.delete(ajuste)
        db.session.commit()
        flash("Se restauró la fecha original del calendario.")
    return redirect(url_for("vencimientos.listado", cliente_id=cliente_id, mes=mes or None))
