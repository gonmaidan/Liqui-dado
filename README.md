# app-liquidar

Genera la liquidación de IVA de un cliente (débito fiscal, crédito fiscal,
saldo) a partir de sus comprobantes de ventas y compras, y produce:

- Un Excel (`liquidacion.xlsx`) con hoja Resumen (fórmulas, no valores fijos)
  y detalle de Ventas / Compras.
- Un PDF (`resumen.pdf`) prolijo para mandarle al cliente.
- Los 4 TXT de ancho fijo para importar al **Libro IVA Digital de AFIP**
  (comprobantes y alícuotas, de ventas y de compras) — ver más abajo.

Es una app **multi-tenant**: cada estudio contable tiene su propio login y
solo ve sus propios clientes — ningún estudio puede ver los clientes ni
los datos de otro. Además hay un login de **administrador** (el dueño de
la plataforma) para dar de alta estudios, que a propósito **no** tiene
acceso a los datos fiscales de ningún cliente, solo a cuántos tiene
cargados cada estudio.

Hay dos formas de usarla:

- **Como servicio hospedado** (`app.py`): con login, pensada para que
  varios estudios la usen — es la forma recomendada, ver más abajo cómo
  desplegarla en Render + Neon.
- **Desde la terminal** (`main.py`): para uso individual en tu propia
  computadora, sin login ni base de datos — útil para probar rápido o
  automatizar la liquidación de un cliente puntual.

## Contraseñas

- **¿Olvidaste tu contraseña?** — desde cualquiera de los dos logins (estudio
  o admin) hay un link a `/recuperar`. Envía por email un link de un solo
  uso, válido 1 hora, para elegir una contraseña nueva. Funciona igual para
  Estudio que para Admin.
- **Cambiarla estando logueado** — `Mi cuenta`, arriba a la derecha en
  cualquier pantalla, pide la contraseña actual + la nueva dos veces.

El envío de emails usa SMTP (`email_utils.py`), configurable con las
variables `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` /
`EMAIL_FROM` (ver `.env.example`). Se recomienda **Resend**
([resend.com](https://resend.com)) — tiene plan gratis permanente (3.000
emails/mes) y habla SMTP, así que no hace falta ningún SDK. Si esas
variables no están configuradas (por ejemplo, en desarrollo local sin
cuenta de email todavía), el link se imprime por consola en vez de
enviarse — para poder probar el flujo completo sin depender de eso.

## Uso como servicio hospedado (`app.py`)

### 1. Puesta en marcha local (para desarrollar/probar)

```bash
python -m venv venv
source venv/bin/activate     # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env         # y completá SECRET_KEY (DATABASE_URL puede quedar vacío: usa sqlite local)

flask --app app init-db      # crea las tablas
flask --app app crear-admin  # te pide email/nombre/contraseña y crea TU login de admin

python app.py                # http://127.0.0.1:5050
```

Con el login de admin creado, entrá a `http://127.0.0.1:5050/admin/login`,
dado de alta el primer estudio (te va a mostrar una contraseña temporal
para pasarle), y desde `http://127.0.0.1:5050/login` ese estudio ya puede
entrar, cargar sus clientes (`Mis clientes`) y empezar a liquidar.

### 2. Desplegar en producción — Render (app) + Neon (base de datos), gratis

**Base de datos (Neon):**
1. Creá una cuenta en [neon.tech](https://neon.tech) (tiene plan gratis
   permanente, sin tarjeta).
2. Creá un proyecto, y copiá la "Connection string" (empieza con
   `postgresql://`).

**App (Render):**
1. Subí esta carpeta a un repositorio de GitHub.
2. En [render.com](https://render.com), "New" → "Web Service", conectá el
   repo. Render detecta `requirements.txt` y el `Procfile` solo (Start
   Command: `gunicorn app:app`).
3. En "Environment", agregá:
   - `SECRET_KEY`: una clave random y larga (ver comentario en `.env.example`).
   - `DATABASE_URL`: la connection string de Neon del paso anterior.
   - `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `ADMIN_NOMBRE`: tus datos para el
     usuario de administrador. **El plan gratis de Render no tiene Shell**
     (eso es de los planes pagos), así que no se puede correr
     `flask crear-admin` a mano ahí — en su lugar, la primera vez que la
     app arranca, si no existe ningún admin todavía, se crea uno solo con
     estos datos. Los reinicios/deploys siguientes no le vuelven a tocar
     la contraseña, así que después de la primera vez podés borrar estas
     tres variables (o dejarlas, da lo mismo).
   - `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` /
     `EMAIL_FROM`: opcionales, para que funcione "olvidé mi contraseña"
     (ver sección **Contraseñas**).
4. Deploy. Las tablas se crean solas al arrancar, no hace falta ningún
   comando manual.
5. Listo: entrás a `https://tu-app.onrender.com/admin/login` con
   `ADMIN_EMAIL` / `ADMIN_PASSWORD`, y desde ahí das de alta estudios.

El plan gratis de Render "duerme" la app a los 15 minutos sin uso (tarda
~1 minuto en despertar la próxima vez que alguien entra) — para el uso de
unos pocos estudios al mes es un compromiso razonable; el día que haga
falta que esté siempre despierta, se sube al plan pago ($7/mes aprox.).

## Uso individual desde la terminal (`main.py`, sin login)

Para probar rápido, o para tu propio uso sin pasar por el login — no
depende de la base de datos ni del resto de lo de arriba.

### 1. Instalación (una sola vez)

Necesitás Python 3.10+ instalado. Abrí esta carpeta en VS Code, abrí una
terminal (`Ctrl + ñ` o `Terminal > New Terminal`) y corré:

```bash
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # Mac/Linux

pip install -r requirements.txt
```

### 2. Generar las plantillas de carga manual

Si no tenés el export de AFIP y necesitás cargar los comprobantes a mano:

```bash
python generar_plantillas.py
```

Esto crea `plantillas/plantilla_ventas.xlsx` y `plantillas/plantilla_compras.xlsx`.
Cada una trae una fila de ejemplo en amarillo (bórrenla) y una leyenda con
el formato esperado. Guardá una copia por cliente en `datos/`.

### 3. Preparar los datos de cada cliente

Tenés dos opciones para los archivos de ventas y compras (podés mezclar,
uno de cada fuente):

**a) Carga manual:** completar la plantilla del paso 2 con los datos del cliente.

**b) Export de AFIP:** descargar el CSV/Excel del "Libro IVA Digital" o
"Mis Comprobantes" y usarlo directo. El script reconoce los nombres de
columna más comunes de AFIP (ver `COLUMNAS_ALIAS` en `calculo_iva.py`).
Si AFIP cambia un nombre de columna o aparece uno nuevo, agregalo ahí.

Columnas mínimas que el script necesita (con estos nombres exactos, en
minúscula, si armás el archivo vos mismo sin usar la plantilla):

| Columna | Descripción |
|---|---|
| `fecha` | Fecha del comprobante |
| `tipo_comprobante` | Ej: "Factura A", "Factura B" |
| `cuit` | CUIT de la contraparte |
| `razon_social` | Nombre/Razón social de la contraparte |
| `neto_gravado` | Importe neto gravado |
| `iva` | IVA del comprobante |

Estas 6 alcanzan para la liquidación de IVA (Excel + PDF). Si además
querés los TXT para AFIP (ver más abajo), agregá también:

| Columna | Descripción |
|---|---|
| `punto_venta` | Punto de venta tal como figura impreso en el comprobante |
| `numero_comprobante` | Número de comprobante tal como figura impreso |

### 4. Correr la liquidación

```bash
python main.py --cliente "Nombre del Cliente" --periodo "2026-08" \
    --ventas datos/ventas_nombrecliente.csv \
    --compras datos/compras_nombrecliente.csv \
    --estudio "Nombre de su Estudio"
```

Los archivos generados quedan en `salidas/`, nombrados con el cliente y
el período. Podés correr esto una vez por cliente por mes — es el próximo
candidato natural a automatizar más (por ejemplo, un script que recorra
automáticamente todos los clientes de una carpeta).

## TXT para el Libro IVA Digital de AFIP

Si tus archivos de ventas y compras traen las columnas `punto_venta` y
`numero_comprobante` (además de las 6 mínimas), el sistema arma
automáticamente los 4 TXT que pide AFIP para importar en el portal del
Libro IVA Digital, y los suma al mismo ZIP que ya descargás (interfaz web)
o a la carpeta `salidas/` (línea de comandos):

- `..._LIBRO_IVA_DIGITAL_VENTAS_CBTE.txt`
- `..._LIBRO_IVA_DIGITAL_VENTAS_ALICUOTAS.txt`
- `..._LIBRO_IVA_DIGITAL_COMPRAS_CBTE.txt`
- `..._LIBRO_IVA_DIGITAL_COMPRAS_ALICUOTAS.txt`

Si faltan esas columnas, o hay algo que el sistema no sabe interpretar
(un tipo de comprobante raro, una alícuota que no es 0%/2,5%/5%/10,5%/21%/27%),
no se generan los TXT pero **sí** se sigue generando el Excel y el PDF
normalmente — se avisa qué faltó para poder corregirlo.

**Qué cubre esta versión** (el caso típico de un contribuyente chico):
comprobantes en pesos, identificados siempre por CUIT, con una sola
alícuota de IVA por comprobante, percepciones de IVA y de IIBB (una de
cada una por comprobante, columnas opcionales `percepcion_iva` /
`percepcion_iibb`), sin exentos mezclados con gravados, sin importaciones
ni TurIVA.

**Qué NO cubre todavía** (avisen si lo necesitan y lo sumamos):
facturas con varias alícuotas mezcladas, percepciones municipales o de
otros impuestos nacionales, retenciones, ventas a consumidor final sin
CUIT, comprobantes en moneda extranjera, importaciones de bienes/servicios,
comprobantes anulados.

## Cargar comprobantes escaneando el QR de AFIP

Además de subir un Excel/CSV, desde `Mis clientes` → `Escanear factura` el
estudio puede cargar una factura sacándole una foto (o subiendo el PDF) al
código QR que trae toda factura electrónica de AFIP. Es 100% gratis: no
usa ningún servicio de OCR ni de IA de pago. Funciona así:

1. Antes de escanear, se elige si es venta o compra, y la **alícuota de
   IVA** de las facturas que se van a cargar ahora (si son todas al mismo
   %, no hay que volver a elegirla en cada una — se recuerda para la
   próxima). Después se sube la foto/PDF.
2. El sistema lee del QR (que viene firmado digitalmente por AFIP, así que
   es un dato confiable): fecha, CUIT, tipo y número de comprobante, punto
   de venta e importe **total**.
3. En la pantalla siguiente se completa: la razón social de la
   contraparte (se memoriza por CUIT, así que solo se pide la primera
   vez — después ya viene sola) y, si esa factura puntual tiene,
   percepción de IVA y las percepciones de IIBB **una por jurisdicción**
   (jurisdicción + importe; el botón "+ Agregar otra percepción de IIBB"
   suma tantas filas como haga falta). El sistema las suma en un total.
4. El **neto gravado y el IVA se calculan solos**: se restan las
   percepciones del importe total del QR, y sobre lo que queda se aplica
   la alícuota elegida en el paso 1. No hay que tipearlos ni mirarlos en
   ninguna foto — y al restar primero las percepciones, el cálculo cierra
   exacto aunque la factura tenga percepciones.
5. Los comprobantes escaneados quedan guardados por cliente y período, y
   se combinan automáticamente con los de cualquier Excel que subas para
   el mismo período al generar la liquidación en `/generar` — podés usar
   las dos formas juntas.
6. Desde `Ver cargadas` (el listado de comprobantes de un cliente) se
   puede filtrar por período y descargar **dos Excel separados**: uno de
   ventas y otro de compras. La columna `percepcion_iibb` trae el total
   sumado y `detalle_iibb` el desglose por jurisdicción. Si se re-sube un
   Excel exportado a `/generar`, los comprobantes escaneados que ya estén
   en ese Excel (mismo tipo, punto de venta y número) se omiten solos para
   no contarlos dos veces.

**Limitaciones a tener en cuenta:**
- Solo sirve para facturas con una única alícuota de IVA (lo más común
  para un contribuyente chico). Si una factura mezcla alícuotas, hay que
  cargarla directamente por Excel.
- En una venta a consumidor final (sin CUIT), el QR no trae un CUIT
  utilizable — hay que completarlo a mano (podés usar un CUIT/razón social
  genérico que ya uses para esos casos).
- Requiere que el QR se vea nítido y completo en la foto/PDF; con fotos
  borrosas o con reflejos puede no leerlo — en ese caso, avisa y hay que
  probar con otra foto.

Antes de presentar la DDJJ, siempre conviene revisar la vista previa y los
totales que muestra AFIP al importar los archivos.

## Estructura del proyecto

```
liquidacion_iva/
├── app.py                      # App multi-tenant hospedada (Flask + login + DB)
├── extensions.py                # Instancias compartidas (SQLAlchemy, Flask-Login)
├── models.py                    # Estudio, Admin, Cliente, Liquidacion, Comprobante, ContraparteConocida
├── auth.py                      # Login/logout de Estudio y de Admin
├── admin.py                     # Panel de admin (alta/baja de estudios)
├── clientes.py                  # CRUD de clientes de cada estudio
├── escaner.py                   # Carga de comprobantes escaneando el QR de AFIP
├── qr_scanner.py                 # Lectura/decodificación del QR de AFIP (foto o PDF)
├── templates/                   # HTML de la app hospedada
├── Procfile                      # Comando que usa Render para levantar la app
├── .env.example                  # Variables de entorno necesarias
├── main.py                      # Punto de entrada por línea de comandos (uso individual)
├── calculo_iva.py                # Lógica de cálculo (débito/crédito/saldo)
├── reporte_excel.py              # Genera el Excel de liquidación
├── reporte_pdf.py                # Genera el PDF resumen
├── afip_libro_iva.py             # Genera los 4 TXT para el Libro IVA Digital de AFIP
├── generar_plantillas.py         # Genera las plantillas de carga manual
├── requirements.txt
├── plantillas/                    # Plantillas Excel vacías (uso con main.py)
├── datos/                         # Archivos de cada cliente (uso con main.py)
└── salidas/                       # Excel/PDF/TXT generados (uso con main.py)
```

## Próximos pasos sugeridos

**Sobre la liquidación de impuestos en sí:**
- Extender `calculo_iva.py` con Ganancias e Ingresos Brutos (misma lógica,
  otras reglas de cálculo).
- Sumar los casos que hoy `afip_libro_iva.py` no cubre (ver esa sección
  más arriba): varias alícuotas por comprobante, percepciones, consumidor
  final sin CUIT, importaciones.

**Sobre la plataforma multi-tenant (para cuando la uses en serio):**
- No hay límite de intentos de login (rate limiting) — recomendable antes
  de exponerlo públicamente, para evitar fuerza bruta sobre las cuentas
  de los estudios.
- El historial de liquidaciones (`Liquidacion`) hoy guarda solo metadata
  (período, saldo, fecha), no los archivos — si en algún momento quieren
  poder "volver a descargar" una liquidación vieja sin tener que
  regenerarla, ahí conviene sumar almacenamiento persistente (Cloudflare
  R2 o similar) para guardar los archivos.
- Login de admin en "modo soporte" auditado, si algún día necesitás
  entrar a ayudar a un estudio puntual — que quede registrado quién entró
  y cuándo, en vez de acceso silencioso.


## Vencimientos por terminación de CUIT

- El **Admin** carga el calendario base en `/admin/vencimientos` (una línea
  por vencimiento: `impuesto ; fecha ; terminaciones`, ej.
  `IVA ; 18/11/2026 ; 0,1`). Es información pública, sin datos de clientes.
- Cada estudio ve, en `Mis clientes` → `Vencimientos`, los próximos
  vencimientos de ese cliente según la última cifra de su CUIT, ordenados
  por fecha, con filtro por mes.
- Si ARCA prorroga una fecha, el estudio la cambia ahí mismo (con un motivo
  opcional). El cambio es solo de ese estudio, vale para todos sus clientes
  a los que les aplica ese vencimiento, y se puede restaurar el original.
- ARCA no ofrece una API para esto: las prórrogas se publican como
  resolución general, así que hay que actualizar las fechas a mano.

## Tipo de persona y obligaciones por cliente

- Al dar de alta un cliente, el estudio elige **persona física o jurídica** y
  tilda las **obligaciones** que va a trabajar (IVA, Autónomos, Monotributo,
  F.931, Ganancias, Bienes Personales, IIBB CABA, IIBB ARBA, Convenio
  Multilateral). Según el tipo de persona se ofrecen solo las que
  corresponden. Se puede cambiar después con el botón `Obligaciones`.
- La solapa `Vencimientos` del cliente muestra solo los vencimientos del
  calendario del Admin que correspondan a las obligaciones tildadas y a la
  terminación de su CUIT.
- Al cargar el calendario, la app reconoce la obligación por el nombre del
  vencimiento ("IVA...", "F.931...", "IIBB CABA..."). Si no reconoce alguno,
  queda "sin clasificar" en el panel Admin, que lo asigna con un desplegable;
  mientras tanto no se muestra a ningún cliente.
- Las bases creadas con versiones anteriores se actualizan solas al arrancar
  (se agregan las columnas nuevas); los clientes que ya existían quedan como
  persona física y sin obligaciones tildadas.

## Flujo de "Liquidar" en tres pasos

1. **Cliente:** se elige con qué cliente trabajar.
2. **Impuesto:** se muestran las obligaciones tildadas para ese cliente. Hoy
   solo IVA se puede liquidar; el resto aparece como "Próximamente". Para
   sumar un impuesto nuevo: crear su pantalla y agregarlo a `LIQUIDABLES`
   en `app.py`.
3. **Datos del impuesto:** para IVA, período y archivos opcionales
   (los comprobantes escaneados se suman solos).

## Escaneo de varias facturas a la vez

En `Escanear factura` se pueden elegir hasta 20 fotos/PDF juntas. Se leen
todos los QR y aparece una sola pantalla de revisión con un bloque por
factura (uno debajo del otro), cada uno con sus percepciones de IVA e IIBB.
`Guardar todos` guarda todas juntas: las que tengan un error quedan en
pantalla para corregirse sin perder lo ya guardado. Los archivos sin QR
legible y los comprobantes repetidos se avisan y se saltean. Si se escribe
una razón social en un comprobante, se completa sola en los otros de la
misma tanda con el mismo CUIT.

## Número de comprobante: formato y control

El QR de AFIP trae el punto de venta (hasta 5 dígitos) y el número (hasta 8)
como enteros sin ceros a la izquierda; la app los muestra completados
(`00001-00024794`). Si el archivo es un PDF con texto, además se lee el
número impreso en la factura y, si no coincide con el del QR, se muestra un
aviso en la pantalla de revisión para que se verifique antes de guardar.

## Lectura automática del PDF (neto, IVA y percepciones)

Al escanear un **PDF digital** (con texto, no una foto), la app además de leer el QR lee el texto
del PDF e intenta completar sola el neto, el IVA y las percepciones (IVA e IIBB por jurisdicción).

- **Cómo lo hace (`percepciones_pdf.py`)**: toma el total del QR como ancla y busca en el texto una
  combinación que cierre exacta: `total = neto + IVA + percepciones`. El neto y el IVA tienen que estar
  impresos; las percepciones salen de los importes junto a rótulos tipo "Percep. IIBB CABA".
  Si no hay una combinación que cierre (tolerancia de 10 centavos), **no propone nada** y se carga a mano.
- Si la factura cierra con otra alícuota distinta a la elegida (ej. 10,5%), usa esa y lo avisa.
- Si no tocás las percepciones detectadas, se guardan el neto y el IVA **tal como están impresos**
  (pueden diferir centavos de la fórmula). Si las cambiás, se usa la fórmula de siempre.
- Las fotos y PDFs escaneados (imagen) siguen funcionando como antes: percepciones a mano.
- Siempre se muestra un cartel verde "Leímos del PDF…" para que lo verifiques antes de guardar.

**Lector de QR más robusto (`qr_scanner.py`)**: busca el QR en imágenes incrustadas y en las primeras
3 páginas (200/300/400 dpi), lee la URL si está como texto, y acepta QR sin `p=` en la URL.

Pruebas: `python test_percepciones_pdf.py`.

## F.931 (cargas sociales)

Desde **Liquidar → cliente → F.931** (el cliente tiene que tener tildada la obligación F.931):

1. **Datos del empleador**: tipo (Art. 2 inc. a o b → 20,40% o 18%), hasta 25 trabajadores, ART, SCVO, código de actividad/zona, y **qué tipos de empleado tiene** (común, jubilado, jornada parcial).
2. **Empleados**: se cargan una vez (CUIL validado con dígito verificador, obra social, adherentes, hijos, cónyuge, condición y modalidad).
3. **Cada mes**: se cargan sueldo, adicionales, horas extras, zona, vacaciones, premios, SAC, no remunerativo y días. Se guardan y el mes siguiente aparecen copiados.
4. **Descargas**: PDF de presentación (papel de trabajo con nómina, aportes, contribuciones y total a pagar) y **TXT de importación para SICOSS** (registro de largo fijo, 499 posiciones + CRLF).

Cálculo (`f931_calculo.py`): aportes 11% + 3% + 3% (+1,5% por adherente), contribución unificada sobre la base menos la detracción ($7.003,68 por trabajador; +$10.000 por empleador de hasta 25), obra social patronal 6%, ART, SCVO. Base mínima/máxima de ANSES (cargadas: jul y oct 2026; el resto se escribe en la pantalla del período). El mes con SAC multiplica por 1,5 el tope y la detracción.

**Alícuotas, detracciones y topes editables** en `/f931/parametros`.

Límites: es una estimación para controlar contra SICOSS (la DDJJ se presenta desde SICOSS / Declaración en línea). No incluye servicio doméstico, menores de 18, RIFL (régimen de incentivo a nuevas contrataciones), convenios especiales ni reducciones de contribuciones. El formato del TXT sigue el diseño de registro publicado (SIU-Mapuche); **probá la primera importación en SICOSS** y avisá si lo rechaza.

Pruebas: `python test_f931.py`.

**Desplegables del empleado (F.931)**: condición, modalidad de contratación y situación de revista se eligen de una lista con la descripción de cada código (tablas de ARCA, listas parciales; para otro código está «Otro código…»). Son solo informativos: el cálculo usa el *tipo de empleado*. La **obra social** se elige de un desplegable propio de cada estudio: la primera vez se carga código (6 dígitos) y nombre, y queda guardada.

## Convenio Multilateral — CM03 mensual

Desde **Liquidar → cliente → Convenio Multilateral** (el cliente tiene que tener tildada esa obligación):

1. **Configurar** (una vez): jurisdicciones donde tributa (con mínimo opcional) y actividades con su alícuota en cada jurisdicción. Las alícuotas las carga el estudio: la app no trae valores por defecto.
2. **Coeficientes unificados** (CM05): conjuntos con vigencia «desde AAAA-MM»; se valida que sumen 1,0000. Para cada período se usa el último conjunto vigente (los nuevos suelen regir desde abril).
3. **Cada mes**: los ingresos se proponen desde las **facturas de venta cargadas** (neto gravado, con notas de crédito restadas) y las **percepciones de IIBB** desde las **facturas de compra** (por jurisdicción); retenciones, SIRCREB, pagos a cuenta y saldos a favor se cargan a mano. Todo es editable.
4. **Resultado**: base = ingresos × coeficiente; impuesto = base × alícuota (con el mínimo si corresponde); menos deducciones; saldo a pagar o a favor **por jurisdicción** (no se compensan entre sí). Descarga en PDF y Excel (con fórmulas).

Fuera de alcance por ahora: regímenes especiales (CM04, art. 6 a 13), atribución directa de ingresos (art. 14), CM05 anual y presentación en SIFERE (se hace desde SIFERE web con el papel de trabajo).

Pruebas: `python test_cm.py`.

Detalle de percepciones de IIBB (compras) en Excel: botón en la pantalla del CM03.

**TXT de percepciones para SIFERE web** (`sifere_txt.py`, botón en el CM03): un renglón de 51 caracteres por percepción de IIBB de las facturas de compra: jurisdicción (3, códigos 901–924) + CUIT del proveedor (con guiones) + fecha + punto de venta (4) + número (8) + tipo/letra (`FA`) + importe (11, con coma). Va solo lo de jurisdicciones del Convenio del cliente; lo que no se pueda armar (jurisdicción sin identificar, notas de crédito, puntos de venta de 5 dígitos) se avisa y queda afuera.
