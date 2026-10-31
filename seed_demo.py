"""
seed_demo.py
--------------
Carga datos de prueba para poder entrar y trastear con la app sin tener
que crear todo a mano: un admin y dos estudios (app-liquidar y Capemege),
cada uno con sus propios clientes.

Se puede correr las veces que haga falta: si el admin/estudio ya existe
(mismo email), no lo duplica, solo lo deja como está.

    python seed_demo.py

⚠️ Es solo para desarrollo/pruebas — no correr contra la base de
producción con datos reales.
"""

from app import app
from extensions import db
from models import Admin, Estudio, Cliente


def _admin_o_crear(email, nombre, password):
    admin = Admin.query.filter_by(email=email).first()
    if admin is None:
        admin = Admin(email=email, nombre=nombre)
        db.session.add(admin)
    admin.set_password(password)
    return admin


def _estudio_o_crear(email, nombre, password):
    estudio = Estudio.query.filter_by(email=email).first()
    if estudio is None:
        estudio = Estudio(email=email, nombre=nombre)
        db.session.add(estudio)
    estudio.set_password(password)
    return estudio


def _cliente_o_crear(estudio, cuit, razon_social):
    cliente = Cliente.query.filter_by(estudio_id=estudio.id, cuit=cuit).first()
    if cliente is None:
        cliente = Cliente(estudio=estudio, cuit=cuit, razon_social=razon_social)
        db.session.add(cliente)
    return cliente


def main():
    with app.app_context():
        db.create_all()

        admin = _admin_o_crear("admin@liquidado.com", "Administrador", "AdminPass123")
        db.session.flush()

        liqui_dado = _estudio_o_crear("liquidado@estudio.com", "app-liquidar", "Liquidado123")
        capemege = _estudio_o_crear("capemege@estudio.com", "Capemege", "Capemege123")
        db.session.flush()

        _cliente_o_crear(liqui_dado, "20304050607", "Kiosco Don José")
        _cliente_o_crear(liqui_dado, "27111222339", "Panadería La Espiga")
        _cliente_o_crear(capemege, "30111222338", "Ferretería Capemege")

        db.session.commit()

    print("Listo. Podés entrar con:\n")
    print("  Admin      -> http://127.0.0.1:5050/admin/login")
    print("    email: admin@liquidado.com       password: AdminPass123\n")
    print("  app-liquidar -> http://127.0.0.1:5050/login")
    print("    email: liquidado@estudio.com     password: Liquidado123\n")
    print("  Capemege   -> http://127.0.0.1:5050/login")
    print("    email: capemege@estudio.com      password: Capemege123\n")


if __name__ == "__main__":
    main()
