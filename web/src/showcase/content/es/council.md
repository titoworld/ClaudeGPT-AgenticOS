<!-- @question -->
Migración de PostgreSQL 14 a 17 con el mínimo tiempo de parada

Tengo un PostgreSQL 14 con 40 GB de datos en un VPS (Debian 12, 4 vCPU, 8 GB de RAM y 160 GB de disco). La aplicación puede estar parada como mucho 10 minutos. ¿Qué plan me proponéis?
<!-- @claude-answer -->
## `pg_upgrade --link`: 3–5 minutos de parada

Con 40 GB y una ventana de 10 minutos, la opción más segura es **`pg_upgrade` con enlaces duros**: no copia los datos, solo reconstruye el catálogo del sistema, y con este volumen suele tardar menos de un minuto.

1. **Prepara el 17 junto al 14.** Instala `postgresql-17` y comprueba la compatibilidad con `pg_upgrade --check` (extensiones, tipos de datos, codificación).
2. **Haz una copia verificada.** Un `pg_basebackup` la noche anterior, y prueba a restaurarlo: con `--link`, en cuanto arrancas el 17, el clúster del 14 deja de ser fiable.
3. **Detén la aplicación y actualiza:**
   ```bash
   sudo pg_dropcluster 17 main --stop   # el clúster vacío que crea el paquete
   sudo pg_upgradecluster -m upgrade --link 14 main
   ```
4. **Arranca y regenera las estadísticas** con `vacuumdb --all --analyze-in-stages`: sin estadísticas, las primeras consultas pueden ir lentas.

**Parada prevista:** 3–5 minutos, casi todo para detener y arrancar los servicios.
<!-- @chatgpt-answer -->
**Respuesta corta:** con 10 minutos de margen tienes dos opciones válidas. Yo elegiría la **replicación lógica**: la parada se queda en segundos y puedes volver atrás en cualquier momento.

| Opción | Parada | Volver atrás | Riesgo |
| --- | --- | --- | --- |
| `pg_upgrade --link` | 3–5 min | Solo desde una copia | Bajo |
| Replicación lógica | < 1 min | Sí: el 14 sigue vivo | Medio |
| `pg_dump` + `pg_restore` | 30–60 min | Sí | Bajo |

**Pasos con replicación lógica**

- Crea un clúster 17 en el puerto `5433` y copia el esquema con `pg_dump --schema-only`.
- `CREATE PUBLICATION` en el 14 y `CREATE SUBSCRIPTION` en el 17; espera a que termine la copia inicial.
- **El cambio:** detén la aplicación, sincroniza las secuencias (`setval`) y apúntala al puerto `5433`.

> La replicación lógica no copia las secuencias ni los cambios de esquema (DDL): congela las migraciones de la aplicación mientras dure.
<!-- @claude-critique-1 -->
- La replicación lógica es la opción cuando hace falta una parada de segundos, pero aquí el límite es de 10 minutos: `--link` cabe con margen y tiene menos piezas que puedan fallar.
- Necesita el doble de disco (40 GB más: los hay, pero justos) y las tablas sin clave primaria no admiten `UPDATE` ni `DELETE` una vez publicadas: habría que revisarlas una a una.
- Acierta en algo que a mí me faltaba: poder volver atrás. Lo incorporo con un ensayo previo y una copia verificada.
<!-- @claude-revision-1 -->
## `pg_upgrade --link`, ensayado antes

Mantengo la recomendación: con una ventana de 10 minutos, **`pg_upgrade --link`** cabe con margen y tiene menos piezas que puedan fallar que la replicación lógica.

1. **Ensayo completo** en una instantánea del VPS: mide el tiempo real y detecta extensiones incompatibles con `pg_upgrade --check`.
2. **Copia verificada** la noche anterior (`pg_basebackup` y una prueba de restauración): es tu camino de vuelta.
3. **Ventana:** `pg_dropcluster 17 main --stop` y `pg_upgradecluster -m upgrade --link 14 main`.
4. **Después:** arranca la aplicación y ejecuta `vacuumdb --all --analyze-in-stages`.

**Plan B:** si algún día necesitas una parada de segundos, la replicación lógica que propone ChatGPT es el camino, pero antes hay que revisar las tablas sin clave primaria.
<!-- @chatgpt-critique-1 -->
- El plan es correcto y, para una ventana de 10 minutos, más simple que el mío.
- Hay que decir claramente que, con `--link`, una vez arrancado el 17 ya no se puede volver al 14: la única vuelta atrás es la copia.
- Falta un ensayo en una copia del servidor para saber el tiempo real antes de la ventana.
<!-- @chatgpt-revision-1 -->
**Respuesta revisada:** con 10 minutos de margen, **`pg_upgrade --link`** basta y es más simple; la replicación lógica solo compensa si necesitas una parada de segundos.

| | `pg_upgrade --link` | Replicación lógica |
| --- | --- | --- |
| Parada | 3–5 min | < 1 min |
| Disco adicional | Casi nada | +40 GB |
| Volver atrás | Desde la copia | Inmediato |
| Riesgo | Bajo | Medio (DDL, secuencias) |

Antes de la ventana, ensáyalo en una instantánea del VPS y guarda una copia verificada: es la única forma de volver atrás.
<!-- @claude-critique-2 -->
- Ningún error relevante: la respuesta revisada de ChatGPT coincide con la mía, y la tabla añade lo que cuesta en disco cada opción.
<!-- @chatgpt-critique-2 -->
- Ninguna objeción: el plan está completo e incluye el ensayo, la copia verificada y el plan B.
<!-- @synthesis -->
## Plan final: `pg_upgrade --link`, ensayado antes

Claude y ChatGPT coincidimos: con una ventana de 10 minutos, **`pg_upgrade --link`** es la opción más simple y cabe con margen. La replicación lógica queda como plan B por si algún día necesitas una parada de segundos.

**La semana antes**

1. Instantánea del VPS y ensayo completo: mide el tiempo real.
2. `pg_upgrade --check` para detectar extensiones o tipos incompatibles.
3. Copia verificada con `pg_basebackup` la noche anterior, y una prueba de restauración.

**La ventana (unos 5 minutos)**

```bash
sudo systemctl stop app
sudo pg_dropcluster 17 main --stop
sudo pg_upgradecluster -m upgrade --link 14 main
sudo systemctl start app
sudo -u postgres vacuumdb --all --analyze-in-stages   # con la aplicación ya en marcha
```

**Si algo falla:** no vuelvas a arrancar el 14 después de haber arrancado el 17; restaura la copia del paso 3.

| | `pg_upgrade --link` | Replicación lógica (plan B) |
| --- | --- | --- |
| Parada | 3–5 min | < 1 min |
| Disco adicional | Casi nada | +40 GB |
| Complejidad | Baja | Media |
