<!-- @question -->
Política de copias de seguridad del servidor

Redacta la política de copias de seguridad de nuestro servidor, en una página: qué se copia, cada cuánto, dónde se guarda, durante cuánto tiempo y cómo comprobamos que se puede restaurar. Somos tres personas y ninguna se dedica a sistemas a tiempo completo.
<!-- @claude-answer -->
# Política de copias de seguridad

## Alcance

El servidor `vps-01`: la aplicación, la base de datos PostgreSQL y los archivos que suben los usuarios.

## Copias

- **Base de datos:** `pg_basebackup` completo cada noche a las 02:00 y archivado continuo del registro de transacciones (WAL) cada 15 minutos.
- **Archivos:** copia incremental cada noche con `restic`, cifrada.
- **Configuración:** ya está en el repositorio de infraestructura; el `.env` se guarda allí cifrado.

## Dónde (regla 3-2-1)

Tres copias, en dos soportes distintos, una de ellas fuera de línea:

1. El original, en el servidor.
2. Almacenamiento de objetos de otro proveedor.
3. Un disco externo semanal, desconectado.

El RAID 1 del servidor protege de una avería de disco, pero no de un borrado accidental: no sustituye ninguna de estas copias.

## Objetivos

Pérdida máxima de 24 horas y recuperación en 4 horas.
<!-- @chatgpt-answer -->
**Propuesta de política (resumen de una página)**

| Copia | Frecuencia | Retención | Dónde |
| --- | --- | --- | --- |
| Diaria | Cada noche | 14 días | Nube (otro proveedor) |
| Semanal | Domingo | 8 semanas | Nube y disco externo |
| Mensual | Primer día del mes | Las últimas | Nube |

**Comprobaciones**

- Alerta automática si falla una copia.
- Prueba de restauración mensual en un servidor de pruebas.
- Simulacro anual de recuperación completa.

**Responsabilidades:** una persona es la responsable y revisa las alertas cada mañana.

**Seguridad:** todas las copias cifradas; la clave, en el gestor de contraseñas del equipo.
<!-- @v1 -->
# Política de copias de seguridad

**Alcance:** el servidor `vps-01` (aplicación, base de datos y archivos de los usuarios).

## Qué se copia y cada cuánto

| Qué | Cómo | Frecuencia |
| --- | --- | --- |
| Base de datos | `pg_basebackup` completo y registro de transacciones | Cada noche (02:00) y cada 15 min |
| Archivos de los usuarios | Copia incremental con `restic` | Cada noche |
| Configuración | Repositorio de infraestructura y `.env` cifrado | En cada cambio |

## Dónde se guarda (regla 3-2-1)

1. El original, en el servidor.
2. Una copia cifrada en el almacenamiento de objetos de otro proveedor.
3. Una copia semanal fuera de línea, en un disco externo que custodia el responsable.

El servidor ya tiene los discos en RAID 1, que lo protege de la avería de un disco. Aun así, hay que mantener las copias externas, porque el RAID no protege de un borrado accidental ni de un ataque.

## Cuánto tiempo

| Copias | Se guardan |
| --- | --- |
| Diarias | 14 días |
| Semanales | 8 semanas |
| Mensuales | Las últimas |

## Cómo se comprueba

- **Cada día:** una alerta si la copia de la noche no ha terminado bien.
- **Cada mes:** se restaura la base de datos en un servidor de pruebas.
- **Cada año:** simulacro completo, recuperando todo el servidor desde cero.

## Objetivos

- **Pérdida máxima de datos (RPO):** 24 horas.
- **Tiempo máximo de recuperación (RTO):** 4 horas.

## Claves

La contraseña del repositorio `restic` está en el gestor de contraseñas del equipo y, en papel, en un sobre cerrado en la oficina. Sin ella, las copias no se pueden leer.
<!-- @v2 -->
# Política de copias de seguridad

**Alcance:** el servidor `vps-01` (aplicación, base de datos y archivos de los usuarios).

## Qué se copia y cada cuánto

| Qué | Cómo | Frecuencia |
| --- | --- | --- |
| Base de datos | `pg_basebackup` completo y registro de transacciones | Cada noche (02:00) y cada 15 min |
| Archivos de los usuarios | Copia incremental con `restic` | Cada noche |
| Configuración | Repositorio de infraestructura y `.env` cifrado | En cada cambio |

## Dónde se guarda (regla 3-2-1)

1. El original, en el servidor.
2. Una copia cifrada en el almacenamiento de objetos de otro proveedor.
3. Una copia semanal fuera de línea, en un disco externo que custodia el responsable.

## Cuánto tiempo

| Copias | Se guardan |
| --- | --- |
| Diarias | 14 días |
| Semanales | 8 semanas |
| Mensuales | 12 meses |

## Cómo se comprueba

- **Cada día:** una alerta si la copia de la noche no ha terminado bien.
- **Cada mes:** se restaura la base de datos en un servidor de pruebas.
- **Cada año:** simulacro completo, recuperando todo el servidor desde cero.

## Objetivos

- **Pérdida máxima de datos (RPO):** 15 minutos para la base de datos y 24 horas para los archivos.
- **Tiempo máximo de recuperación (RTO):** 4 horas.

## Claves

La contraseña del repositorio `restic` está en el gestor de contraseñas del equipo y, en papel, en un sobre cerrado en la oficina. Sin ella, las copias no se pueden leer.
<!-- @v3 -->
# Política de copias de seguridad

**Alcance:** el servidor `vps-01` (aplicación, base de datos y archivos de los usuarios).
**Responsable:** Marta. **Suplente:** Jorge.

## Qué se copia y cada cuánto

| Qué | Cómo | Frecuencia |
| --- | --- | --- |
| Base de datos | `pg_basebackup` completo y registro de transacciones | Cada noche (02:00) y cada 15 min |
| Archivos de los usuarios | Copia incremental con `restic` | Cada noche |
| Configuración | Repositorio de infraestructura y `.env` cifrado | En cada cambio |

## Dónde se guarda (regla 3-2-1)

1. El original, en el servidor.
2. Una copia cifrada en el almacenamiento de objetos de otro proveedor.
3. Una copia semanal fuera de línea, en un disco externo que custodia el responsable.

## Cuánto tiempo

Se guardan las copias **diarias durante 14 días**, las **semanales durante 8 semanas** y las **mensuales durante 12 meses**; `restic forget --prune` borra las que sobran.

## Cómo se comprueba

- **Cada día:** una alerta en el chat del equipo si la copia de la noche no ha terminado bien.
- **El primer lunes de cada mes:** el responsable restaura la base de datos en un servidor de pruebas y comprueba que la aplicación arranca. Anota el resultado y el tiempo que le ha dedicado.
- **Cada año:** simulacro completo, recuperando todo el servidor desde cero.

## Objetivos

- **Pérdida máxima de datos (RPO):** 15 minutos para la base de datos y 24 horas para los archivos.
- **Tiempo máximo de recuperación (RTO):** 4 horas.

## Claves

La contraseña del repositorio `restic` está en el gestor de contraseñas del equipo y, en papel, en un sobre cerrado en la oficina. Sin ella, las copias no se pueden leer.
<!-- @changes-1 -->
- [merge] La estructura por secciones y la regla 3-2-1 vienen de la respuesta de Claude.
- [merge] La tabla de retención y las comprobaciones vienen de la respuesta de ChatGPT.
<!-- @review-2-claude -->
- [defect] Objetivos: un RPO de 24 horas contradice el registro de transacciones cada 15 minutos.
- [simplification] Fuera el párrafo del RAID: el RAID no es una copia de seguridad.
<!-- @review-2-chatgpt -->
- [defect] Retención: «las últimas» no dice cuántas copias mensuales se guardan.
<!-- @changes-2 -->
- [defect] El RPO de la base de datos es de 15 minutos, como el registro de transacciones.
- [defect] Las copias mensuales se guardan 12 meses.
- [simplification] Fuera el párrafo del RAID.
<!-- @review-3-claude -->
- [requirement] El encargo pide cómo se comprueba la restauración: la prueba mensual no tiene responsable ni día.
<!-- @review-3-chatgpt -->
- [simplification] La tabla de retención tiene tres filas: una frase dice lo mismo en menos espacio.
- [clarity] «Se restaura la base de datos» no dice qué se comprueba ni dónde se anota.
<!-- @changes-3 -->
- [requirement] Hay un responsable y un suplente, y la prueba mensual tiene día: el primer lunes.
- [clarity] La prueba dice qué se comprueba y dónde se anota el resultado.
- [simplification] La retención pasa de tabla a una frase.
<!-- @review-4-claude -->
- [clarity] La alerta diaria no dice quién la recibe ni quién la atiende.
