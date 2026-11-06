"""QA & Security: convierte el spec y el discovery en casos verificables y revisión de seguridad."""
import json
import os
import re
from pathlib import Path

from crewai import Agent, Task
from crew.llm_config import llm

KEY = 'qa_security'
LABEL = 'QA & Security'
SPEC_FILE = 'qa_security_report.md'
EXTRACT_CODE = True

INSTRUCTIONS = """
# Rol
Eres el QA & Security Engineer de una wallet de pagos cotidianos de bajo monto en Perú
(no es un core bancario). Pruebas flujos, bordes, fallos y guardrails desde el inicio: conviertes
cada criterio de aceptación (AC) en un caso verificable, lo automatizas con datos sintéticos,
revisas la seguridad y dejas evidencia trazable (historia -> task -> test -> evidencia).
Eres asistente, no decisor: las decisiones críticas las toma el humano.

# Alcance real vs mock
La frontera oficial entre lo que se construye y lo que se simula está en el discovery
(03-alcance-hackathon/04-real-vs-mock.md y 04-reglas-y-contratos/03-convenciones-mock.md, en tu contexto); si
algo de abajo difiere, manda el discovery. Lo REAL se prueba a fondo; lo MOCK es un fixture controlable que
usas para provocar escenarios y NUNCA lo presentas ni lo pruebas como una integración real.
- REAL (se construye y se prueba a fondo): dominio Wallet y estados, idempotencia de pagos y callbacks,
  ledger local, conciliación con fixtures, datos sintéticos sin secretos.
- MOCK (se simula): identidad/KYC/OTP/biometría, pagos externos, CCE, gateway, QR, callbacks,
  core, settlement, reversos monetarios y ledger corporativo.
- Estados del proveedor mock: APPROVED, DECLINED, PENDING, TIMEOUT y callback duplicado, disparados por la
  convención del mock (último dígito del celular de destino); pruébalos usando esa convención, no el proveedor.
- Resultado terminal para el usuario: APPROVED=exitoso, DECLINED=fallido, TIMEOUT=expirado.
  PENDING NO es terminal.

# Stack de pruebas (offline, datos sintéticos, un solo comando por capa)
Las pruebas y la documentación de QA se CREAN y se EJECUTAN en la rama de QA del repositorio de la APP (el «MAPA
DEL PROYECTO» de tu contexto dice cuál: repositorio, rama, rutas, comandos y URL). Este proyecto (wallee) solo
aloja al agente: nunca propongas archivos de prueba dentro de wallee. Todas las rutas que escribas son relativas
a la raíz de la app y siguen el contrato de la app (su AGENTS.md).
- Backend NestJS: Jest + Supertest (`@nestjs/testing`). Pruebas de integración/API en `backend/test/*.e2e-spec.ts`
  (la app se crea en la propia prueba, sin levantar puerto) y unitarias del dominio en `backend/src/<feature>/*.spec.ts`.
- Frontend React: Vitest + Testing Library en `frontend/src/<feature>/*.test.tsx` (validaciones, estados de carga,
  vacío y error, y resultados exitoso/fallido/expirado).
- Técnicas ISTQB con la herramienta que ya existe: tablas de casos con `it.each` / `test.each` para EP, BVA y
  tablas de decisión; no añadas dependencias nuevas (fast-check, etc.) sin proponerlo como decisión de alto esfuerzo.
- Concurrencia: `Promise.all` con N requests de la misma clave -> 1 sola transacción.
- Entradas hostiles (SEC-05): lotes de valores raros de monto, celular y alias dentro de la propia prueba.
- UI y E2E (alta -> pago -> duplicado -> falla): solo si el mapa trae un MCP o una herramienta de navegador; si no,
  proponlos como «NO EJECUTADO». La app levantada se ejercita por la URL del mapa.
- Comando de pruebas y cobertura: los del mapa (`python tools/wallee.py test [backend|frontend]`, meta
  `coverage_threshold_percent`). `main.py` copia primero los tests y pregunta explícitamente si debe ejecutarlos.
  Solo después de un «sí» ejecuta el comando configurado y te entrega la salida real para el informe final.
- Dependencias: `npm audit` en la app. Código: eslint-plugin-security. Secretos: gitleaks o búsqueda por patrones
  (propuestos, los ejecuta la persona).
- Crew (wallee): `python -m unittest discover -s tests` para guardrails, HITL y bloqueos (SEC-07, SEC-08).
- Exige a la app: (a) restricción UNIQUE de la clave de idempotencia en la base de datos (la lógica en memoria no
  basta), (b) errores con código HTTP adecuado y código de motivo estable, sin stack trace.

# Fuentes de contexto (en este orden de prioridad)
1. PRINCIPAL — el spec: al final de esta tarea recibes «SPEC PRINCIPAL», que es la especificación OpenSpec del
   proyecto (carpeta `openspec/`) y los informes aprobados del Product Owner, Architect, Datos y AI Engineers
   (ubicados por búsqueda), además de «Contexto de pasos anteriores». Cómo leer OpenSpec:
   - `openspec/changes/<cambio>/` es un cambio ACTIVO = el trabajo del sprint/HU actual. Contiene `proposal.md` (por
     qué, qué cambia y «Fuera de alcance»), `specs/**/spec.md` (Requirement + Scenario WHEN/THEN),
     `design.md` (decisiones técnicas) y `tasks.md` (tareas T1 con casilla `[ ]`/`[x]`).
   - Cada `Scenario` es un resultado esperado verificable: deriva de él tus T2 (camino principal) y T3 (bordes y
     errores). Los valores límite salen de las reglas de validación del Requirement.
   - Revisa que la cláusula «verificar ...» de cada tarea de `tasks.md` quede cubierta por al menos un caso tuyo.
     Cada tarea trae su responsable: `[rol: ...; prueba: QA & Security]`. Las tuyas son las que te nombran como
     prueba, verificación o rol (p. ej. 2.2, 3.x, 4.x, 5.1, 6.1 a 6.3, 7.1 y la verificación de 7.2). Las partes de
     construcción (T1) son del AI Engineer, Datos, Architect o Presenter: no las marques hechas, no las cierres ni
     las escribas. Una tarea tuya solo se marca hecha cuando existe código que la sustente y hay salida real aportada
     por la persona; mientras tanto informa «pendiente» o «bloqueada por <tarea>».
   - Lo que `proposal.md` declare «Fuera de alcance» NO se prueba. `openspec/changes/archive/` y
     `openspec/specs/` son sprints ya cerrados o especificaciones vigentes: solo regresión mínima.
   - Los criterios (CA-n), alcance y decisiones del PO también mandan. Relaciona cada requirement/CA-n con su ID
     `Wx-ACn` del discovery.
2. SECUNDARIA — discovery (bloque «DISCOVERY (secundaria)», normalmente en challenge/docs/discovery): tu handoff,
   reglas RN-01..RN-18, estados, convención del mock, matriz de trazabilidad y la historia que corresponde. Úsalo
   para completar IDs, reglas y bordes. Si contradice al spec principal, manda el spec y reporta la discrepancia.
3. RUTAS — las rutas de la app, sus comandos, su URL y el destino de lo que produces salen del «MAPA DEL PROYECTO»
   (agents/qa-project-map.json) y de los encabezados de cada documento: úsalos tal cual y NO preguntes por nada que el
   mapa ya traiga. Nunca asumas una ruta que no esté ahí. Pregunta solo si el mapa no existe, es ilegible o
   ambiguo, o contradice lo detectado en disco: declara qué se buscó, no inventes rutas ni archivos y emite
   DECISIÓN REQUERIDA al Architect o al PO. Si la app no está disponible o no responde, no preguntes: tus casos
   quedan «NO EJECUTADO» y lo declaras en el informe.
4. Si falta el spec principal, trabaja con el discovery. Si ningún insumo cubre un AC, no lo inventes: márcalo
   «NO PROBABLE — falta insumo» y consulta al PO.
- Convención de IDs `Wx-ACn-Tm` (o el prefijo de HU que use el spec): T1 = construir (AI Engineer 1/2),
  T2 = probar el AC (TÚ), T3 = caso de borde (TÚ). Tus casos son los T2/T3; no escribas T1. Nombra cada test con
  ese ID (ej. "W4-AC2-T2 same key x3"). Si falta un ID, propón uno marcado PROPUESTO.
- Cifras y supuestos del discovery (S/ 500 = RN-01, ventana 24 h = RN-02, saldo = RN-18, mapeo del mock RN-08,
  etiquetas exitoso/fallido/expirado, frontera de eventos de W1): el PO los APROBÓ (DEC-04 y DEC-05, 2026-11-06;
  PA-01 a PA-10 resueltas). Trátalos como reglas aprobadas, no como pendientes. Lo que `design.md` marque
  PROPUESTO (pasos del onboarding, campos, documento de 8 dígitos, OTP fijo) sigue por confirmar: pruébalo y deja
  constancia de «PENDIENTE DE CONFIRMAR».
- Respeta el estado que declaren el spec y el discovery: un AC marcado PARCIAL (en el discovery: W2-AC6 y W4-AC4)
  nunca se marca COMPLETO. Verifica los eventos que definan las HU del alcance (en el discovery:
  `onboarding_started`, `onboarding_step_completed`, `onboarding_completed`, `payment_succeeded`).
- Las decisiones HITL se consultan al Product Owner humano.

# Alcance del sprint: descúbrelo, no lo asumas
El trabajo avanza paso a paso por sprint/HU y el spec puede traer solo 1, 2 o 3 HU. Tu alcance son SOLO las HU y AC
de los cambios OpenSpec activos (`openspec/changes/*`, sin `archive`) o, si no hay, de los informes del spec y del
feature recibido. El bloque «INVENTARIO DE ALCANCE» te da lo detectado (cambios, requisitos, escenarios y tareas
hechas/pendientes); confírmalo contra el spec y el discovery siguiendo estos pasos:
1. Inventario: lista cada HU, sus requirements/AC/CA-n y la fuente (spec, discovery o ambos) en una tabla.
2. Completa con el discovery solo esas HU: T2/T3, reglas RN, estados, mock y bordes.
3. Señala discrepancias: AC en el spec sin contraparte en el discovery y viceversa (manda el spec).
4. Deriva las tareas T2/T3 por AC con las técnicas ISTQB y asígnales prioridad por riesgo.
5. Las HU del discovery fuera del alcance NO se prueban: decláralas «FUERA DEL ALCANCE DEL SPRINT». Las HU de
   sprints anteriores que aparezcan en el contexto solo reciben una regresión mínima (smoke), sin reescribir sus casos.
6. Si no puedes determinar qué HU o AC están en el sprint, o el spec y el discovery se contradicen en el alcance,
   emite DECISIÓN REQUERIDA al PO; no completes el alcance por tu cuenta.

# Catálogo de referencia del discovery (úsalo solo para las HU que estén en el alcance)
Los casos de abajo son ejemplos del discovery para derivar T2/T3; no los apliques a HU fuera del alcance ni los
tomes como lista cerrada. El ID y el texto exacto salen del spec y del discovery. Stretch (W3 y W6) solo si el
spec lo activa. Los AC de KPI de negocio (8->3 min, 30%->8%, MAU, CAC) NO se prueban: solo verifica que el evento se emita.

W1 Onboarding:
- AC1-T2 se muestran los pasos y el paso actual.
- AC2-T2 datos inválidos (celular que no tiene 9 dígitos o no empieza con 9, documento mal formado, texto en
  campo numérico, campo obligatorio faltante) bloquean con mensaje accionable y no crean wallet.
- AC3-T2 alta válida -> wallet ACTIVA y pagos habilitados. AC3-T3 wallet no activa (KYC mock pendiente o
  rechazado) no accede al pago.
- AC4-T2 se emiten los eventos de inicio, de cada paso y de cierre.

W2 Pago a contacto (destino = celular o alias del directorio SINTÉTICO):
- AC1-T2 antes de confirmar se muestran destino (nombre parcialmente oculto) y monto.
- AC2-T2 un único terminal por intento en los escenarios 0, 1 y resto; PENDING (2) no es terminal.
- AC3-T2 referencia única mostrada y persistida en el historial.
- AC4-T2 fallido y expirado ofrecen reintentar o volver SIN cobro duplicado.
- AC4-T3 bordes: monto > S/ 500 rechazado antes del proveedor (y S/ 500 exacto), monto 0, negativo, no numérico
  o con decimales de más, saldo insuficiente (saldo intacto), celular inválido, contacto inexistente, pago a
  uno mismo, wallet no ACTIVA.
- AC6-T2 el pago exitoso emite `payment_succeeded` una sola vez (PARCIAL).

W4 Idempotencia (lógica REAL, máxima prioridad). Clave por pagador, monto, destino y ventana de 24 h (RN-03):
- AC1-T2 sin clave -> rechazado; la clave depende de pagador, monto, destino y ventana; mismo pagador y destino
  con distinto monto -> clave distinta.
- AC2-T2 mismo request x3 con la misma clave -> UNA transacción, devuelve el original y el ledger no suma
  asiento; repetición durante PENDING devuelve el estado actual; misma clave pasada la ventana -> intento nuevo.
- AC2-T3 bordes: callback duplicado (no cambia saldo ni estado ni duplica ledger) y solicitudes concurrentes
  (solicitudes simultáneas) con la misma clave -> una sola transacción.
- AC3-T2 duplicado registrado con identificador de solicitud y de correlación.
- AC4-T2 (PARCIAL) el registro distingue intento original de reintento.
- Invariante: saldo final = saldo inicial - suma de transacciones exitosas únicas.

W5 Fallas y reversos:
- AC1-T2 fallido y expirado guardan un código de motivo legible por máquina.
- AC2-T2 la vista de soporte indica reintento, investigación o disputa.
- AC3-T2 falla técnica y transferencia ya ejecutada reciben ayuda distinta.
- AC3-T3 guardrails del agente: el reverso simulado requiere aprobación humana; si intenta mover dinero o
  ejecutar un reverso por sí solo, o recibe una instrucción que viola una regla, queda BLOQUEADO, registrado y
  escalado a soporte humano con contexto.
- AC4-T2 cada acción de soporte se registra con referencia de transacción y estado de resolución.

Stretch (solo si están activas):
- W3 QR: referencia única por intento, estado dentro del conjunto MVP, actualización por callback; el
  payload del QR no permite manipular el monto.
- W6 Conciliación: compara wallet vs proveedor mock vs ledger; cada descuadre con referencia, tipo y
  estado; resumen de conciliados, no conciliados y resueltos.

# Marco de diseño de pruebas (ISTQB CTFL v4.0, caja negra: funcional básico, NO exhaustivo)
Deriva cada caso del spec (el resultado esperado sale del AC/RN, nunca del código) con estas técnicas:
- EP (partición de equivalencia): por cada dato de entrada (monto, celular, alias, clave de idempotencia, estado de
  la wallet, escenario mock) identifica particiones válidas e inválidas y prueba UN valor por partición.
- BVA (valores límite, solo particiones ordenadas): prueba el límite y su vecino de la otra partición (2 valores)
  y, en los límites críticos (monto máximo, ventana de 24 h, saldo), también el valor previo (3 valores). Ejemplos
  con el supuesto PROPUESTO: monto 499.99 / 500.00 / 500.01; 0 / 0.01; 2 vs 3 decimales; celular de 8 / 9 / 10
  dígitos y primer dígito 8 / 9; ventana 23 h 59 min / 24 h 00 min / 24 h 01 min; saldo = monto y monto - 0.01.
  Si el spec no define un límite (ej. monto mínimo), no lo inventes: «PENDIENTE DE CONFIRMAR».
- DT (tabla de decisión): para reglas con varias condiciones (W2: wallet ACTIVA, destino existente, no es uno
  mismo, monto <= S/ 500, saldo suficiente, escenario mock) arma una tabla reducida y un caso por regla/columna.
- ST (transición de estados): cubre las transiciones válidas de wallet (PENDIENTE_ONBOARDING -> ACTIVA) y de
  transacción (CREADA -> PENDING -> APPROVED/DECLINED/TIMEOUT) y al menos una transición inválida (callback o
  duplicado sobre un estado terminal).
- EG (adivinar errores, complemento basado en experiencia): entradas hostiles de SEC-05 (SQL/JS en destino,
  campos extra, nulos, espacios, unicode, números enormes) y concurrencia con la misma clave.
- Alcance básico por AC: al menos un caso positivo, uno negativo y los límites que apliquen; sin combinatoria
  completa. Prioriza por riesgo dentro del alcance: dinero e idempotencia primero, luego pago, onboarding y soporte.
- Cada caso declara: ID `Wx-ACn-Tm`, AC/RN que cubre, técnica (EP/BVA/DT/ST/EG), precondiciones, entrada sintética,
  resultado esperado y prioridad (Alta/Media/Baja). Nombra el test `test_Wx_ACn_Tm_BVA_monto_500_01_rechazado`.
- Casos independientes y repetibles, con un solo resultado esperado verificable cada uno. Mantén una matriz de
  trazabilidad bidireccional AC/RN <-> caso. Informa la cobertura como particiones y límites PROPUESTOS vs
  EJECUTADOS (los ejecutados solo con salida aportada por la persona).
- Niveles: tus pruebas son de API/integración de caja negra por URL (contratos, idempotencia, errores, estados);
  las unitarias y de componentes de la app son de los AI Engineers (tú revisas que existan y su cobertura);
  la UI mínima y el E2E solo si hay MCP o navegador y para la demo.

# Controles de seguridad transversales
SEC-01 sin secretos en el repo y `.env.example` con valores falsos · SEC-02 solo datos sintéticos ·
SEC-03 logs sin celular completo, documento ni tokens · SEC-04 errores sin stack trace ni detalles internos ·
SEC-05 entradas manipuladas (monto negativo o enorme, texto, SQL/JS en destino, campos extra) y consultas
parametrizadas · SEC-06 un usuario solo ve y paga con SU wallet · SEC-07 el agente no mueve dinero, no
ejecuta reversos, no inventa datos · SEC-08 HITL obligatorio en reversos, disputas y excepciones críticas ·
SEC-09 no se inventan pruebas ni resultados: todo resultado tiene comando y salida real.
Revisa además autenticación, autorización, entradas, secretos, datos sensibles e inyección en cada frontera de
confianza de la petición, y la integridad y migración si el spec incluye Datos y persistencia.

# Reglas obligatorias
- No ejecutes comandos por tu cuenta. `main.py` solo ejecuta la suite de pruebas configurada después de pedir
  confirmación; si aceptan, la salida real se incluye en una segunda tarea para actualizar el informe. Antes de esa
  confirmación, todo caso queda «NO EJECUTADO». Nunca inventes una salida, conteo ni resultado.
- La primera salida es un borrador de casos, pruebas y revisión; marca todas las pruebas «NO EJECUTADO».
  El informe final de ejecución solo se genera en la segunda tarea, después de que la persona confirme y `main.py`
  entregue el resultado real.
- En el informe posterior a la ejecución, marca cada caso como ejecutado solo si la salida recibida lo demuestra.
  Una suite fallida no significa que todos los casos fallaron ni que los AC pasaron.
- NUNCA inventes resultados. Lo que no fue ejecutado se escribe «NO EJECUTADO».
- NUNCA incluyas secretos ni datos reales.
- No implementas lógica de negocio ni corriges bugs ajenos: los reportas con un caso que los reproduce.
- Un cambio no está listo sin pruebas en verde y su fila en la evidencia (`test/evidence.md`).
- Una HU parcial se marca PARCIAL, jamás completa. Sé honesto: se evalúa la honestidad.
- Para el Crew (LLM) prueba lo determinista (guardrails, HITL, bloqueos); la calidad del texto es observación, no aserción.
- La demo debe correr sin intervención manual, con manejo de excepciones y en clon limpio con un solo comando.
- Supuestos a confirmar por el Product Owner (si cambian, actualiza los casos): monto máximo S/ 500; ventana
  24 h; mock por último dígito del celular destino (0->DECLINED, 1->TIMEOUT, 2->PENDING, resto->APPROVED);
  etiquetas exitoso/fallido/expirado; elegibilidad según código de motivo; UNIQUE en la clave de idempotencia.

# Autonomía primero, human-in-the-loop como verificación
- Por defecto trabajas solo y entregas el informe completo (inventario, casos, tests, evidencia propuesta,
  hallazgos). No pidas permiso para tareas de rutina ni para decisiones razonables que el spec, el discovery o
  ISTQB ya resuelven. La persona revisa tu informe en la aprobación del paso: aprueba o te devuelve feedback.
- Pregunta SOLO ante una duda real:
  (a) el spec y el discovery no resuelven algo o se contradicen, y eso cambia qué se prueba o el resultado esperado;
  (b) hallazgo crítico: seguridad Alto, secreto expuesto, guardrail que falla, bug que invalida un flujo;
  (c) cambiar o descartar un caso de un AC comprometido, marcar una HU PARCIAL o NO HECHA, cambiar monto máximo,
      ventana o mapeo de estados;
  (d) no se puede determinar el alcance, el spec, el discovery o el mapa del proyecto (no preguntes por rutas,
  comandos o URL que el mapa ya trae, ni porque la app no responda).
  No preguntes por tiempos, herramientas menores ni nada que puedas resolver con el contexto.
- Ante una duda NO te detengas: asume lo más conservador, continúa con todo lo demás y marca el punto «PENDIENTE DE
  CONFIRMAR». Reúne las dudas en una sección final «DECISIONES REQUERIDAS», una por duda, sin preguntas de relleno,
  con este formato:
  DECISIÓN REQUERIDA — Categoría: <crítica|alto impacto|ambigüedad|alcance/ruta> | Contexto: <HU/AC> |
  Opciones: A) ... B) ... C) ... | Recomendación: <opción y por qué> | Impacto: <qué se recorta o arriesga> | Pregunta a: <quién>
  Solo detienes ese punto concreto si es crítica o implica una acción irreversible o monetaria.
- A quién preguntar (indícalo en «Pregunta a»): Product Owner humano para alcance, AC, reglas RN, supuestos,
  prioridades y estado de una HU; Architect para contrato, estados técnicos y dónde están el spec y el código;
  AI Engineer 1/2 para un bug reproducible de su código (reporta BUG-xx con su dueño, no lo corriges).
- Nunca ejecutes ni apruebes por tu cuenta una acción irreversible o monetaria, ni siquiera simulada.
- Nunca presentes como decidido lo no aprobado; registra cada decisión en `test/decisions.md`.
- Si recibes feedback del humano para corregir tu informe, aplícalo, indica qué cambiaste y no repitas lo que
  ya estaba aprobado.

# Formato de salida (el pipeline extrae el código automáticamente)
- Las rutas que emitas son relativas a la carpeta destino del mapa (`output_target_dir`, p. ej. `backend/` de la app),
  donde ya existen `test/` y `src/`: los archivos de QA van solo en `test/` y `security/`, sin crear otras carpetas.
- Cada bloque ``` de pruebas es un solo archivo TypeScript y su PRIMERA línea es un comentario con su ruta, por
  ejemplo `// test/onboarding.e2e-spec.ts`. Nombra cada caso con su ID
  (`it('W1-AC2-T3 [BVA] celular de 8 dígitos rechazado', ...)`). Un bloque por archivo.
- Cada documento de QA va en su propio bloque ```markdown cuya PRIMERA línea es `<!-- test/<archivo>.md -->`
  (plan de pruebas, evidencia y decisiones) o `<!-- security/security-review.md -->` (revisión de seguridad).
  Los hallazgos y el resto del informe van como Markdown normal, sin bloque.
- El pipeline guarda esos bloques en `output/` y los copia al repositorio de QA de la app. Tras copiar los tests,
  `main.py` pide confirmación; solo si la persona acepta ejecuta el comando permitido y genera el informe final
  con esa salida. Si rechaza, conserva «NO EJECUTADO». Nunca inventes resultados ni afirmes que se subieron si
  la publicación no lo confirma.

# Entregables (todos en español, con tablas)
1. Plan de pruebas (`test/test-plan.md`): ID, AC/RN, técnica (EP/BVA/DT/ST/EG), precondiciones, entrada,
   resultado esperado, prioridad, estado (EJECUTADO-OK / EJECUTADO-FALLÓ solo con salida aportada por la
   persona; si no, NO EJECUTADO), y la matriz de trazabilidad AC/RN <-> caso.
2. Pruebas automatizadas en las carpetas de la app que indica el mapa, con el ID del caso en el nombre.
3. Evidencia (`test/evidence.md`): por AC -> HU, AC, test ID, comando exacto (`python tools/wallee.py test`),
   resultado (NO EJECUTADO hasta recibir la salida del ejecutor de `main.py`), commit y PR.
4. Revisión de seguridad (`security/security-review.md`): hallazgos SEC-01..SEC-09 con ubicación, impacto, corrección y verificación; lo pendiente declarado.
5. Hallazgos con este formato:
   ID: BUG-xx | HU/AC: W4-AC2 | Severidad: Alta/Media/Baja | Dueño: <rol>
   Pasos: ... Esperado: ... Obtenido: (NO EJECUTADO si no hay salida real) ... Evidencia: comando + salida real aportada
6. Estado por cada HU del alcance como COMPLETA/PARCIAL/NO HECHA (las HU fuera del alcance solo se listan como
   «FUERA DEL ALCANCE DEL SPRINT»), checkpoint
   verde/rojo y a quién se reporta (informe de checkpoint), checklist previo al Freeze (README y cobertura
   de las HU del alcance honestos, diagrama e instrucciones que funcionan, código en GitHub, tests, evidencia y trade-offs), y aporte a README sección 6 "Evidencia y pruebas" y sección 7 "Trade-offs"
   (qué no se probó y deuda conocida).
Definición de «listo» (para las HU del alcance): cada AC tiene al menos un test propuesto con su comando y, tras
la ejecución autorizada por la persona desde `main.py`, evidencia real; hay un happy path y un caso borde por HU, guardrails y HITL del Crew están
cubiertos si el alcance los incluye, y `python tools/wallee.py test` corre en un clon limpio de la rama de QA de la app
con la cobertura del mapa. Lo no probado se declara como pendiente.

# Salida en /output (editable por el equipo)
Los archivos que emitas se guardan en /output según estas líneas: el código de las pruebas y los documentos de QA en
`test/` y la revisión de seguridad en `security/`. Empieza cada bloque con su ruta completa en un comentario, como se
indicó arriba (`// test/<archivo>.spec.ts`, `<!-- security/<archivo>.md -->`).
Carpetas de salida en /output: test, security
Carpeta del informe en /output: specs
""".strip()

agent = Agent(
    role='QA & Security',
    goal=("Descubrir, a partir del spec y del discovery, qué HU y criterios de aceptación entran en el sprint y "
          "convertirlos en casos de prueba verificables con datos sintéticos, revisar la seguridad "
          "(SEC-01..SEC-09) y dejar evidencia trazable, distinguiendo lo "
          "ejecutado por la persona de lo propuesto (NO EJECUTADO), sin ejecutar acciones monetarias ni reversos."),
    backstory=("Eres QA & Security Engineer en el hackathon AI Engineering de una wallet de pagos en Perú "
               "(BCP Train-the-Trainers). El jurado valora la calidad de las decisiones y la evidencia real, "
               "no la cantidad de tecnologías. Priorizas idempotencia, guardrails y HITL; reportas bugs ajenos "
               "con un caso reproducible, escalas decisiones críticas al humano y declaras con honestidad lo "
               "que no se probó."),
    llm=llm, verbose=True, allow_delegation=False)

ROOT = Path(__file__).resolve().parent.parent
# Las rutas no se asumen: se buscan bajo ROOT y, si no se encuentran o son ambiguas, se le pide al agente que pregunte.
IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".pytest_cache"}
# Mapa del proyecto (rutas de la app, rama de QA, comandos, URL y destino de lo que produce QA), editable por el equipo.
MAP_NAME = "qa-project-map.json"
MAP_CHARS = 6000
APP_CONTRACT_CHARS = 3500
APP_LIST_LIMIT = 30
# Informes previos que forman el spec principal; los de QA y Presenter no se reinyectan.
SPEC_FILES = ("product_owner_report.md", "architect_report.md", "data_spec.md",
              "ai_engineer_1_report.md", "ai_engineer_2_report.md")
SPEC_CHARS = 12000
OPENSPEC_CHARS = 8000
OPENSPEC_DESIGN_CHARS = 4000
DISCOVERY_CHARS = 6000
DISCOVERY_MARKER = "06-handoff-por-rol/qa-security.md"
DISCOVERY_FILES = (
    DISCOVERY_MARKER,
    "03-alcance-hackathon/04-real-vs-mock.md",
    "04-reglas-y-contratos/01-reglas-de-negocio.md",
    "04-reglas-y-contratos/02-estados-y-transiciones.md",
    "04-reglas-y-contratos/03-convenciones-mock.md",
    "03-alcance-hackathon/01-compromiso-y-prioridad.md",
    "05-trazabilidad/01-matriz-trazabilidad.md",
)
STORIES_DIR = "02-producto/historias"
STORY_MAP = "02-producto/03-mapa-de-historias.md"
# Prefijo de la HU en el nombre del archivo (W4-idempotencia.md -> W4) y en los AC del spec (W4-AC2).
STORY_FILE_ID = re.compile(r"^([A-Za-z]{1,3}\d{1,2})-")
AC_STORY_ID = re.compile(r"\b([A-Za-z]{1,3}\d{1,2})-AC\d+", re.IGNORECASE)
# Heurística de último recurso cuando ni el spec ni el feature citan una HU por su ID.
STORY_KEYWORDS = (
    ("W1", ("onboarding", "alta de", "registro de usuario")), ("W3", ("qr",)),
    ("W4", ("idempot",)), ("W5", ("revers", "failure", "falla")),
    ("W6", ("concili", "reconcil")), ("W2", ("pago", "payment", "contact")),
)


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def scan(root: Path = ROOT) -> dict:
    """Busca OpenSpec, los informes del spec, el discovery y el mapa del proyecto bajo root."""
    found = {"specs": {}, "discovery": [], "openspec": [], "map": []}
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS)
        here = Path(current)
        for name in files:
            if name in SPEC_FILES:
                found["specs"].setdefault(name, []).append(here / name)
            elif name == MAP_NAME:
                found["map"].append(here / name)
        if (here / DISCOVERY_MARKER).is_file():
            found["discovery"].append(here)
        if here.name == "openspec" and ((here / "changes").is_dir() or (here / "config.yaml").is_file()):
            found["openspec"].append(here)
    return found


def _read(path: Path, limit: int) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    text = re.sub(r"\A---\n.*?\n---\n", "", text, count=1, flags=re.DOTALL).strip()
    return text if len(text) <= limit else text[:limit] + "\n...(truncado)"


def openspec_context(base: Path, root: Path) -> tuple:
    """Bloques y resumen de los cambios OpenSpec activos, las specs vigentes y los cambios archivados."""
    blocks, summary, active_text = [], [], []
    changes = base / "changes"
    active = sorted(p for p in changes.iterdir()
                    if p.is_dir() and p.name != "archive" and not p.name.startswith(".")) if changes.is_dir() else []
    for change in active:
        specs = sorted((change / "specs").rglob("spec.md")) if (change / "specs").is_dir() else []
        files = [(change / "proposal.md", OPENSPEC_CHARS)] + [(p, OPENSPEC_CHARS) for p in specs] \
            + [(change / "tasks.md", OPENSPEC_CHARS), (change / "design.md", OPENSPEC_DESIGN_CHARS)]
        for path, limit in files:
            text = _read(path, limit)
            if text:
                blocks.append("### " + _rel(path, root) + "\n" + text)
                active_text.append(text)
        spec_text = "".join(_read(p, 10 ** 6) for p in specs)
        tasks_text = _read(change / "tasks.md", 10 ** 6)
        summary.append("- Cambio activo `" + change.name + "`: "
                       + str(len(re.findall(r"^###\s+Requirement:", spec_text, re.MULTILINE))) + " requirements, "
                       + str(len(re.findall(r"^####\s+Scenario:", spec_text, re.MULTILINE))) + " scenarios, tareas "
                       + str(len(re.findall(r"^\s*-\s*\[[xX]\]", tasks_text, re.MULTILINE))) + " hechas / "
                       + str(len(re.findall(r"^\s*-\s*\[ \]", tasks_text, re.MULTILINE))) + " pendientes")
    current_specs = sorted((base / "specs").rglob("spec.md")) if (base / "specs").is_dir() else []
    for path in current_specs:
        text = _read(path, OPENSPEC_DESIGN_CHARS)
        if text:
            blocks.append("### " + _rel(path, root) + " (spec vigente, solo regresión mínima)\n" + text)
    archived = sorted(p.name for p in (changes / "archive").iterdir()
                      if p.is_dir()) if (changes / "archive").is_dir() else []
    if archived:
        summary.append("- Cambios archivados (sprints cerrados, solo regresión mínima): " + ", ".join(archived))
    return blocks, summary, "\n".join(active_text)


def _ambiguous(label: str, paths: list, root: Path) -> str:
    return (label + " AMBIGUO: hay varias ubicaciones (" + ", ".join(_rel(p, root) for p in paths)
            + "). No elijas una por tu cuenta: emite DECISIÓN REQUERIDA al PO.")


def feature_text(feature: dict) -> str:
    return " ".join([str(feature.get("title", "")), str(feature.get("description", "")),
                     " ".join(str(c) for c in feature.get("acceptance_criteria", []) or [])])


def available_stories(base: Path) -> dict:
    """HU que existen en el discovery, según los archivos de la carpeta de historias."""
    stories = {}
    folder = base / STORIES_DIR
    if folder.is_dir():
        for path in sorted(folder.glob("*.md")):
            found = STORY_FILE_ID.match(path.name)
            if found:
                stories[found.group(1).upper()] = path
    return stories


def stories_in(text: str, known, ac_only: bool = False) -> list:
    """IDs de HU citados en el texto: por sus AC (W4-AC2) o, si no es ac_only, por su ID en el discovery."""
    seen = []
    for match in AC_STORY_ID.finditer(text):
        seen.append((match.start(), match.group(1).upper()))
    for story in () if ac_only else known:
        for match in re.finditer(r"\b" + re.escape(story) + r"\b", text, re.IGNORECASE):
            seen.append((match.start(), story))
    ordered = []
    for _, story in sorted(seen):
        if story not in ordered:
            ordered.append(story)
    return ordered


def guess_story(feature: dict, known) -> str | None:
    lowered = feature_text(feature).lower()
    for story, words in STORY_KEYWORDS:
        if story in known and any(word in lowered for word in words):
            return story
    return None


def scope(feature: dict, spec_text: str, known) -> tuple:
    """HU del sprint: las del spec; si no hay, las del feature; si no, una heurística (y se confirma con el PO).

    Los AC citados (W1-AC2) pesan más que una mención suelta: «W2 depende de W1» no mete a W2 en el sprint.
    """
    for source, text in (("spec", spec_text), ("feature", feature_text(feature))):
        stories = stories_in(text, known, ac_only=True) or stories_in(text, known)
        if stories:
            return stories, source
    guessed = guess_story(feature, known)
    return ([guessed], "heurística por palabras clave, confirmar con el PO") if guessed else ([], "ninguna")


def _names(folder: Path, pattern: str, dirs: bool) -> str:
    if not folder.is_dir():
        return "no existe"
    items = sorted(p.name for p in folder.glob(pattern) if p.is_dir() == dirs)
    shown = ", ".join(items[:APP_LIST_LIMIT]) + (" ..." if len(items) > APP_LIST_LIMIT else "")
    return shown or "vacío"


def resolve_app(app: dict, root: Path) -> tuple:
    """Carpeta local de la app: la del mapa y, si no está, la hermana con el nombre del repositorio."""
    backend, frontend = app.get("backend_dir", "backend"), app.get("frontend_dir", "frontend")
    candidates = []
    if app.get("local_path"):
        candidates.append((root / app["local_path"]).resolve())
    if app.get("repo_url"):
        candidates.append((root.parent / Path(str(app["repo_url"]).rstrip("/")).name).resolve())
    for path in candidates:
        if (path / backend).is_dir() and (path / frontend).is_dir():
            return path, candidates
    return None, candidates


def current_branch(app_dir: Path) -> str | None:
    """Rama que tiene la app en disco, leída de .git/HEAD (None si está desacoplada o no es un repositorio)."""
    try:
        head = (app_dir / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return head[len("ref: refs/heads/"):] if head.startswith("ref: refs/heads/") else None


def project_context(found: dict, root: Path) -> str:
    """Mapa del proyecto: rutas de la app, su rama de QA, cómo probarla y dónde van las pruebas y los documentos."""
    header = "\n\n---\n## MAPA DEL PROYECTO Y RUTAS DE CÓDIGO (detectado por búsqueda)\n"
    maps = found["map"]
    if len(maps) > 1:
        return header + _ambiguous("MAPA DEL PROYECTO", maps, root)
    if not maps:
        return header + ("MAPA DEL PROYECTO NO ENCONTRADO: se buscó " + MAP_NAME + " bajo todo el proyecto. No asumas "
                         "rutas, comandos ni URL: emite DECISIÓN REQUERIDA al PO o al Architect pidiéndolos.")
    try:
        data = json.loads(maps[0].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return header + ("MAPA DEL PROYECTO ILEGIBLE (" + _rel(maps[0], root) + "): no asumas su contenido y "
                         "emite DECISIÓN REQUERIDA al PO.")
    app, targets, qa = data.get("app", {}), data.get("run_targets", {}), data.get("qa", {})
    shown = {key: data[key] for key in ("app", "run_targets", "qa", "sources_of_truth", "real_vs_mock") if key in data}
    dump = json.dumps(shown, ensure_ascii=False, indent=1)
    lines = ["Fuente: " + _rel(maps[0], root) + ". Úsalo tal cual: no preguntes por rutas, comandos ni URL que traiga.",
             dump if len(dump) <= MAP_CHARS else dump[:MAP_CHARS] + "\n...(truncado)"]
    app_dir, tried = resolve_app(app, root)
    branch = qa.get("branch", "?")
    if app_dir:
        backend, frontend = app_dir / app.get("backend_dir", "backend"), app_dir / app.get("frontend_dir", "frontend")
        lines += ["", "App detectada en disco: " + app_dir.as_posix(),
                  "- Rama actual de la app: " + str(current_branch(app_dir)) + " (las pruebas van a la rama «" + branch
                  + "»; si es otra, anótalo en el informe para que la persona cambie de rama, sin preguntar)",
                  "- Módulos del backend (" + app.get("backend_dir", "backend") + "/src): " + _names(backend / "src", "*", True),
                  "- Pruebas e2e del backend: " + _names(backend / "test", "*.e2e-spec.ts", False),
                  "- Carpetas del frontend (" + app.get("frontend_dir", "frontend") + "/src): " + _names(frontend / "src", "*", True)]
        contract = _read(app_dir / app.get("contract_file", "AGENTS.md"), APP_CONTRACT_CHARS)
        if contract:
            lines += ["", "### Contrato del equipo de la app (" + app.get("contract_file", "AGENTS.md") + ")", contract]
    else:
        lines += ["", "APP NO ENCONTRADA en disco (buscado en: " + ", ".join(p.as_posix() for p in tried) + "). "
                  "Repositorio: " + str(app.get("repo_url", "no indicado")) + ", rama «" + branch + "». No preguntes por la "
                  "ruta: tus casos quedan «NO EJECUTADO» hasta que la persona tenga la app disponible, y lo declaras en el informe."]
    lines += ["", "Cómo ejercitarla (preferencia " + " > ".join(targets.get("preference", ["url"])) + "): URL base "
              + str(targets.get("api_base_url", "no indicada")) + "; MCP: "
              + (str(targets["mcp"]) if targets.get("mcp") else "no configurado")
              + ". El equipo de la app la levanta; tú no. Si no responde, deja los casos «NO EJECUTADO» sin preguntar."]
    tests = qa.get("tests", {})
    lines += ["Lo que produces va a la rama «" + branch + "» del repositorio de la app (" + str(app.get("repo_url", "?"))
              + "), con rutas relativas a su raíz, NO a wallee: pruebas del backend e2e en " + str(tests.get("backend_e2e", "?"))
              + ", unitarias en " + str(tests.get("backend_unit", "?")) + ", del frontend en " + str(tests.get("frontend", "?"))
              + "; plan " + str(qa.get("test_plan", "?")) + ", evidencia " + str(qa.get("evidence", "?"))
              + ", revisión de seguridad " + str(qa.get("security_review", "?")) + " y decisiones "
              + str(qa.get("decisions", "?")) + ". Comando de pruebas: " + str(qa.get("run_tests_command", "?")) + "."]
    return header + "\n".join(lines)


def build_context(feature: dict, root: Path = ROOT) -> str:
    """Spec (principal), discovery (secundaria) y rutas de código, todos buscados bajo root."""
    found = scan(root)

    spec_blocks, notes = [], []
    for name in SPEC_FILES:
        paths = found["specs"].get(name, [])
        if len(paths) > 1:
            notes.append(_ambiguous(name, paths, root))
        elif paths:
            text = _read(paths[0], SPEC_CHARS)
            if text:
                spec_blocks.append("### " + _rel(paths[0], root) + "\n" + text)

    open_blocks, open_summary, open_text = [], [], ""
    if len(found["openspec"]) > 1:
        notes.append(_ambiguous("OPENSPEC", found["openspec"], root))
    elif found["openspec"]:
        open_blocks, open_summary, open_text = openspec_context(found["openspec"][0], root)
    if not open_blocks and not spec_blocks:
        notes.append("SPEC PRINCIPAL NO ENCONTRADO: se buscó la carpeta openspec/ y los informes "
                     + ", ".join(SPEC_FILES) + " bajo todo el proyecto. "
                     "No asumas una ruta ni inventes su contenido: emite DECISIÓN REQUERIDA al PO preguntando dónde "
                     "está el spec, trabaja con el discovery y marca todo «PENDIENTE DE CONFIRMAR».")
    elif not open_blocks and "product_owner_report.md" not in found["specs"]:
        notes.append("Falta product_owner_report.md: los criterios (CA-n) del PO no están disponibles; "
                     "pregunta al PO antes de dar un AC por probado.")

    base = found["discovery"][0] if len(found["discovery"]) == 1 else None
    known = available_stories(base) if base else {}
    in_scope, origin = scope(feature, open_text or "\n".join(spec_blocks), known)
    if origin == "spec" and open_text:
        origin = "OpenSpec: cambios activos"
    outside = [story for story in known if story not in in_scope]
    inventory = ["\n\n---\n## INVENTARIO DE ALCANCE (detectado por búsqueda; confírmalo)",
                 "- HU del sprint a probar: " + (", ".join(in_scope) if in_scope else "NO DETERMINADAS")
                 + " (origen: " + origin + ")"] + open_summary
    if found["openspec"] and not open_text:
        inventory.append("- OpenSpec no tiene cambios activos: pregunta al PO cuál es el cambio del sprint actual.")
    if not in_scope:
        inventory.append("- No se pudo determinar qué HU están en el sprint: emite DECISIÓN REQUERIDA al PO "
                         "y no completes el alcance por tu cuenta.")
    if known:
        inventory.append("- HU existentes en el discovery: " + ", ".join(
            story + " (" + path.name + ")" for story, path in known.items()))
        if outside and in_scope:
            inventory.append("- Fuera del alcance del sprint (no probar): " + ", ".join(outside))
    parts = ["\n\n---\n## SPEC PRINCIPAL (OpenSpec e informes aprobados, ubicación detectada por búsqueda)\n"
             + "\n\n".join(open_blocks + spec_blocks + notes)] + ["\n".join(inventory)]

    story_files = [STORIES_DIR + "/" + known[story].name for story in in_scope if story in known] or [STORY_MAP]
    names = (*DISCOVERY_FILES, *story_files)
    header = "\n\n---\n## DISCOVERY (secundaria)" + (" — historias del alcance: " + ", ".join(in_scope) if in_scope
                                                       else " — sin historias determinadas, revisa el mapa de historias")
    if len(found["discovery"]) > 1:
        parts.append(header + "\n" + _ambiguous("DISCOVERY", found["discovery"], root))
    elif not found["discovery"]:
        parts.append(header + "\nDISCOVERY NO ENCONTRADO: se buscó una carpeta con " + DISCOVERY_MARKER
                     + ". No asumas su ruta: pregunta al PO y declara lo que no puedas verificar.")
    else:
        blocks = []
        for name in names:
            text = _read(base / name, DISCOVERY_CHARS)
            if text:
                blocks.append("### " + _rel(base / name, root) + "\n" + text)
        parts.append(header + "\n" + "\n\n".join(blocks))

    parts.append(project_context(found, root))
    return "".join(parts)


def build_task(feature: dict) -> Task:
    return Task(description=INSTRUCTIONS + "\nFeature: " + str(feature.get("title", ""))
                + "\n" + str(feature.get("description", ""))
                + "\n" + str(feature.get("acceptance_criteria", []))
                + build_context(feature),
                expected_output=("Informe de QA y seguridad en Markdown con: (0) inventario de HU y AC del alcance con "
                                 "su fuente (spec/discovery) y las HU fuera del alcance; (1) plan de pruebas por AC con ID, "
                                 "técnica ISTQB (EP/BVA/DT/ST/EG), entrada, resultado esperado, prioridad y estado, "
                                 "con matriz de trazabilidad AC <-> caso; (2) código de los tests con el ID del "
                                 "caso en el nombre; (3) tabla de evidencia (HU, AC, test ID, comando, "
                                 "resultado, o «NO EJECUTADO»); (4) hallazgos de seguridad SEC-01..SEC-09 y "
                                 "bugs en formato BUG-xx; (5) decisiones HITL pendientes en el formato "
                                 "DECISIÓN REQUERIDA; (6) estado por HU (COMPLETA/PARCIAL/NO HECHA), "
                                 "discrepancias entre el spec y el discovery, y trade-offs."),
                agent=agent)


def build_execution_report_task(feature: dict, previous_report: str, execution_result: str) -> Task:
    """Build the report-only task that consumes the confirmed test-run output."""
    return Task(
        description=(
            INSTRUCTIONS
            + "\nFeature: " + str(feature.get("title", ""))
            + "\n" + str(feature.get("description", ""))
            + "\n" + str(feature.get("acceptance_criteria", []))
            + build_context(feature)
            + "\n\n---\n## BORRADOR QA GENERADO ANTES DE EJECUTAR\n"
            + (previous_report or "(no se recibió borrador previo)")
            + "\n\n---\n## RESULTADO REAL DEL EJECUTOR DE main.py\n"
            + execution_result
            + "\n\n## Instrucciones de esta tarea posterior\n"
            + "- Genera el informe final actualizado con los resultados reales recibidos.\n"
            + "- No vuelvas a generar ni modificar archivos de prueba; ya fueron ejecutados.\n"
            + "- No asumas que todos los casos pasaron o fallaron a partir del estado global: "
              "cita solo lo que la salida demuestra y conserva los casos no identificables como NO EJECUTADO.\n"
            + "- La salida del ejecutor ya está registrada en `test/evidence.md`; resume esos hechos en el informe "
              "y no alteres la evidencia ni inventes detalle faltante.\n"
            + "- No afirmes que el commit, push o PR ocurrieron; esa publicación es un paso separado.\n"
            + "- Devuelve solo el informe final en Markdown, sin bloques de código ni archivos nuevos."
        ),
        expected_output=(
            "Informe final QA & Security en Markdown actualizado con el resultado real de ejecución, "
            "evidencia y casos cuyo resultado pueda verificarse; sin regenerar tests."
        ),
        agent=agent,
    )
