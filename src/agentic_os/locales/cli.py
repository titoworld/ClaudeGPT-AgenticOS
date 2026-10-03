"""Texts of cli.py and admin.py: the command line (keys ``cli.*``)."""

from __future__ import annotations

from typing import Final

from agentic_os.i18n import Text

MESSAGES: Final[dict[str, Text]] = {
    "cli.quoted": {"en": '"{text}"', "es": "«{text}»", "ca": "«{text}»"},
    "cli.either": {"en": "{first} or {last}", "es": "{first} o {last}", "ca": "{first} o {last}"},
    "cli.both": {"en": "{first} and {last}", "es": "{first} y {last}", "ca": "{first} i {last}"},
    "cli.db_unavailable": {
        "en": "Could not open the database {path}: {error}",
        "es": "No se ha podido abrir la base de datos {path}: {error}",
        "ca": "No s'ha pogut obrir la base de dades {path}: {error}",
    },
    # -- Help (build_parser) ----------------------------------------------------------
    "cli.help.usage": {"en": "usage:", "es": "uso:", "ca": "ús:"},
    "cli.help.options": {"en": "options", "es": "opciones", "ca": "opcions"},
    "cli.help.help": {
        "en": "show this help and exit",
        "es": "muestra esta ayuda y sale",
        "ca": "mostra aquesta ajuda i surt",
    },
    "cli.help.description": {
        "en": "ClaudeGPT OS: Claude and ChatGPT answer, critique each other and synthesize a "
        "better answer.",
        "es": "ClaudeGPT OS: Claude y ChatGPT responden, se critican y sintetizan una "
        "respuesta mejor.",
        "ca": "ClaudeGPT OS: Claude i ChatGPT responen, es critiquen i sintetitzen una "
        "resposta millor.",
    },
    "cli.help.version": {
        "en": "show the version and exit",
        "es": "muestra la versión y sale",
        "ca": "mostra la versió i surt",
    },
    "cli.help.commands": {"en": "commands", "es": "órdenes", "ca": "ordres"},
    "cli.help.command": {"en": "COMMAND", "es": "ORDEN", "ca": "ORDRE"},
    "cli.help.serve": {
        "en": "Start the web server.",
        "es": "Arranca el servidor web.",
        "ca": "Arrenca el servidor web.",
    },
    "cli.help.host": {
        "en": "address to listen on (default: AOS_HOST)",
        "es": "dirección en la que escuchar (por defecto, AOS_HOST)",
        "ca": "adreça on escoltar (per defecte, AOS_HOST)",
    },
    "cli.help.port": {
        "en": "port to listen on (default: AOS_PORT)",
        "es": "puerto en el que escuchar (por defecto, AOS_PORT)",
        "ca": "port on escoltar (per defecte, AOS_PORT)",
    },
    "cli.help.dev": {
        "en": "development: detailed logging and configuration hints. It does not relax the "
        "cookies: for local http set AOS_SECURE_COOKIES=false",
        "es": "desarrollo: registro detallado y consejos de configuración. No relaja las "
        "cookies: para http local define AOS_SECURE_COOKIES=false",
        "ca": "desenvolupament: registre detallat i consells de configuració. No relaxa les "
        "cookies: per a http local defineix AOS_SECURE_COOKIES=false",
    },
    "cli.help.init": {
        "en": "Set up the owner: password and TOTP code.",
        "es": "Configura el propietario: contraseña y código TOTP.",
        "ca": "Configura el propietari: contrasenya i codi TOTP.",
    },
    "cli.help.reset_sessions": {
        "en": "Close every open session, forget the known devices and clear the login lockouts.",
        "es": "Cierra todas las sesiones abiertas, olvida los dispositivos conocidos y borra "
        "los bloqueos de inicio de sesión.",
        "ca": "Tanca totes les sessions obertes, oblida els dispositius coneguts i esborra els "
        "bloquejos d'inici de sessió.",
    },
    "cli.help.reset_throttle": {
        "en": "Clear the lockouts after failed login attempts (closes no session).",
        "es": "Borra los bloqueos por intentos de inicio de sesión fallidos (no cierra ninguna "
        "sesión).",
        "ca": "Esborra els bloquejos per intents d'inici de sessió fallits (no tanca cap sessió).",
    },
    "cli.help.doctor": {
        "en": "Check the installation, the configuration and the providers.",
        "es": "Comprueba la instalación, la configuración y los proveedores.",
        "ca": "Comprova la instal·lació, la configuració i els proveïdors.",
    },
    # -- Invalid settings (describe_invalid_settings, config.py) ----------------------
    "cli.settings.invalid_title": {
        "en": "The configuration (AOS_* variables) is not valid:",
        "es": "La configuración (variables AOS_*) no es válida:",
        "ca": "La configuració (variables AOS_*) no és vàlida:",
    },
    "cli.settings.env_not_utf8": {
        "en": "Could not read the configuration: the .env file is not UTF-8.",
        "es": "No se ha podido leer la configuración: el archivo .env no es UTF-8.",
        "ca": "No s'ha pogut llegir la configuració: el fitxer .env no és UTF-8.",
    },
    "cli.settings.env_unreadable": {
        "en": "Could not read the configuration (.env file): {error}.",
        "es": "No se ha podido leer la configuración (archivo .env): {error}.",
        "ca": "No s'ha pogut llegir la configuració (fitxer .env): {error}.",
    },
    "cli.settings.must_be": {
        "en": "must be {what}",
        "es": "debe ser {what}",
        "ca": "ha de ser {what}",
    },
    "cli.settings.bound.ge": {
        "en": "at least {value}",
        "es": "como mínimo {value}",
        "ca": "com a mínim {value}",
    },
    "cli.settings.bound.gt": {
        "en": "greater than {value}",
        "es": "mayor que {value}",
        "ca": "més gran que {value}",
    },
    "cli.settings.bound.le": {
        "en": "at most {value}",
        "es": "como máximo {value}",
        "ca": "com a màxim {value}",
    },
    "cli.settings.bound.lt": {
        "en": "less than {value}",
        "es": "menor que {value}",
        "ca": "més petit que {value}",
    },
    "cli.settings.integer": {
        "en": "must be an integer",
        "es": "debe ser un número entero",
        "ca": "ha de ser un nombre enter",
    },
    "cli.settings.number": {
        "en": "must be a number",
        "es": "debe ser un número",
        "ca": "ha de ser un número",
    },
    "cli.settings.boolean": {
        "en": "must be true or false",
        "es": "debe ser true o false",
        "ca": "ha de ser true o false",
    },
    "cli.settings.one_of": {
        "en": "must be one of these values: {values}",
        "es": "debe ser uno de estos valores: {values}",
        "ca": "ha de ser un d'aquests valors: {values}",
    },
    "cli.settings.invalid": {"en": "is not valid", "es": "no es válido", "ca": "no és vàlid"},
    "cli.settings.value": {
        "en": 'value: "{value}"',
        "es": "valor: «{value}»",
        "ca": "valor: «{value}»",
    },
    "cli.settings.json": {
        "en": "must be JSON; for example, a list of origins is written {example}.",
        "es": "debe ser JSON; por ejemplo, una lista de orígenes se escribe {example}.",
        "ca": "ha de ser JSON; per exemple, una llista d'orígens s'escriu {example}.",
    },
    # -- serve ------------------------------------------------------------------------
    "cli.serve.dev": {
        "en": "Development mode: detailed logging.",
        "es": "Modo de desarrollo: registro detallado.",
        "ca": "Mode de desenvolupament: registre detallat.",
    },
    "cli.serve.http_secure_cookies": {
        "en": "Warning: AOS_PUBLIC_ORIGIN is http:// and the cookies are secure. If the browser "
        "does not keep the session, set AOS_SECURE_COOKIES=false (only locally).",
        "es": "Aviso: AOS_PUBLIC_ORIGIN es http:// y las cookies son seguras. Si el navegador "
        "no guarda la sesión, define AOS_SECURE_COOKIES=false (solo en local).",
        "ca": "Avís: AOS_PUBLIC_ORIGIN és http:// i les cookies són segures. Si el navegador "
        "no desa la sessió, defineix AOS_SECURE_COOKIES=false (només en local).",
    },
    "cli.serve.vite_origin": {
        "en": "If you use Vite's server (npm run dev), add its origin: {example}.",
        "es": "Si usas el servidor de Vite (npm run dev), añade su origen: {example}.",
        "ca": "Si fas servir el servidor de Vite (npm run dev), afegeix el seu origen: {example}.",
    },
    # -- doctor -----------------------------------------------------------------------
    "cli.doctor.title": {
        "en": "ClaudeGPT OS {version}: diagnosis",
        "es": "ClaudeGPT OS {version}: diagnóstico",
        "ca": "ClaudeGPT OS {version}: diagnosi",
    },
    "cli.doctor.warning_tag": {"en": "[WARN]", "es": "[AVISO]", "ca": "[AVÍS]"},
    "cli.doctor.bad_origin": {
        "en": 'AOS_PUBLIC_ORIGIN is not a valid origin: "{origin}".',
        "es": "AOS_PUBLIC_ORIGIN no es un origen válido: «{origin}».",
        "ca": "AOS_PUBLIC_ORIGIN no és un origen vàlid: «{origin}».",
    },
    "cli.doctor.origin": {
        "en": "Public origin: {origin}",
        "es": "Origen público: {origin}",
        "ca": "Origen públic: {origin}",
    },
    "cli.doctor.http_secure_cookies": {
        "en": "The origin is http:// but the cookies are secure: the browser will not keep the "
        "session. Use https or, only locally, AOS_SECURE_COOKIES=false.",
        "es": "El origen es http:// pero las cookies son seguras: el navegador no guardará la "
        "sesión. Usa https o, solo en local, AOS_SECURE_COOKIES=false.",
        "ca": "L'origen és http:// però les cookies són segures: el navegador no desarà la "
        "sessió. Fes servir https o, només en local, AOS_SECURE_COOKIES=false.",
    },
    "cli.doctor.insecure_cookies": {
        "en": "AOS_SECURE_COOKIES=false: right only for local development.",
        "es": "AOS_SECURE_COOKIES=false: correcto solo para desarrollo local.",
        "ca": "AOS_SECURE_COOKIES=false: correcte només per a desenvolupament local.",
    },
    "cli.doctor.permissions": {
        "en": "Permissions too open on {path} ({mode}); recommended {expected}: {command}",
        "es": "Permisos demasiado abiertos en {path} ({mode}); recomendado {expected}: {command}",
        "ca": "Permisos massa oberts a {path} ({mode}); recomanat {expected}: {command}",
    },
    "cli.doctor.no_data_dir": {
        "en": 'The data directory {path} does not exist: "agentic-os init" will create it.',
        "es": "El directorio de datos {path} no existe: se creará con «agentic-os init».",
        "ca": "El directori de dades {path} no existeix: es crearà amb «agentic-os init».",
    },
    "cli.doctor.not_a_directory": {
        "en": "{path} is not a directory.",
        "es": "{path} no es un directorio.",
        "ca": "{path} no és un directori.",
    },
    "cli.doctor.data_dir_read_only": {
        "en": "Cannot write to the data directory {path}.",
        "es": "No se puede escribir en el directorio de datos {path}.",
        "ca": "No es pot escriure al directori de dades {path}.",
    },
    "cli.doctor.data_dir": {
        "en": "Data directory: {path}",
        "es": "Directorio de datos: {path}",
        "ca": "Directori de dades: {path}",
    },
    "cli.doctor.no_database": {
        "en": 'There is no database or owner yet: run "agentic-os init".',
        "es": "Todavía no hay base de datos ni propietario: ejecuta «agentic-os init».",
        "ca": "Encara no hi ha base de dades ni propietari: executa «agentic-os init».",
    },
    "cli.doctor.database_failed": {
        "en": "Could not open the database: {error}",
        "es": "No se ha podido abrir la base de datos: {error}",
        "ca": "No s'ha pogut obrir la base de dades: {error}",
    },
    "cli.doctor.no_owner": {
        "en": 'No owner is set up: run "agentic-os init".',
        "es": "No hay ningún propietario configurado: ejecuta «agentic-os init».",
        "ca": "No hi ha cap propietari configurat: executa «agentic-os init».",
    },
    "cli.doctor.owner": {
        "en": "Owner set up (password and TOTP).",
        "es": "Propietario configurado (contraseña y TOTP).",
        "ca": "Propietari configurat (contrasenya i TOTP).",
    },
    "cli.doctor.fx_rate": {"en": "$1 = €{rate}", "es": "1 $ = {rate} €", "ca": "1 $ = {rate} €"},
    "cli.doctor.fx_ecb": {
        "en": "ECB exchange rate of {date}: {rate}.",
        "es": "Tipo de cambio del BCE del {date}: {rate}.",
        "ca": "Tipus de canvi del BCE del {date}: {rate}.",
    },
    "cli.doctor.fx_manual": {
        "en": "Manual exchange rate: {rate}.",
        "es": "Tipo de cambio manual: {rate}.",
        "ca": "Tipus de canvi manual: {rate}.",
    },
    "cli.doctor.fx_fallback": {
        "en": "Fallback manual exchange rate: {rate} (there is no recent ECB rate yet; the "
        "server downloads it when it starts).",
        "es": "Tipo de cambio manual de reserva: {rate} (todavía no hay ningún tipo reciente "
        "del BCE; el servidor lo descarga al arrancar).",
        "ca": "Tipus de canvi manual de reserva: {rate} (encara no hi ha cap tipus recent del "
        "BCE; el servidor el baixa en arrencar).",
    },
    "cli.doctor.no_web": {
        "en": 'The built web interface was not found: run "npm ci && npm run build" in web/ '
        "or set AOS_WEB_DIST.",
        "es": "No se ha encontrado la interfaz web compilada: ejecuta «npm ci && npm run "
        "build» en web/ o define AOS_WEB_DIST.",
        "ca": "No s'ha trobat la interfície web compilada: executa «npm ci && npm run build» a "
        "web/ o defineix AOS_WEB_DIST.",
    },
    "cli.doctor.web": {
        "en": "Web interface: {path}",
        "es": "Interfaz web: {path}",
        "ca": "Interfície web: {path}",
    },
    "cli.doctor.cli_not_needed": {
        "en": "{label} CLI: not needed (mode {mode}).",
        "es": "CLI de {label}: no hace falta (modo {mode}).",
        "ca": "CLI de {label}: no cal (mode {mode}).",
    },
    "cli.doctor.cli_missing": {
        "en": 'The {label} CLI cannot be found ("{path}"): install it or set {variable}.',
        "es": "No se encuentra la CLI de {label} («{path}»): instálala o define {variable}.",
        "ca": "No es troba la CLI de {label} («{path}»): instal·la-la o defineix {variable}.",
    },
    "cli.doctor.cli_silent": {
        "en": "The {label} CLI ({path}) does not answer --version.",
        "es": "La CLI de {label} ({path}) no responde a --version.",
        "ca": "La CLI de {label} ({path}) no respon a --version.",
    },
    "cli.doctor.cli": {
        "en": "{label} CLI: {path} ({version})",
        "es": "CLI de {label}: {path} ({version})",
        "ca": "CLI de {label}: {path} ({version})",
    },
    "cli.doctor.limit": {
        "en": "{window} limit: {used} used ({status}).",
        "es": "Límite de {window}: {used} usado ({status}).",
        "ca": "Límit de {window}: {used} usat ({status}).",
    },
    "cli.doctor.limit_renews": {
        "en": "{window} limit: {used} used, renews on {date} UTC ({status}).",
        "es": "Límite de {window}: {used} usado, se renueva el {date} UTC ({status}).",
        "ca": "Límit de {window}: {used} usat, es renova el {date} UTC ({status}).",
    },
    "cli.doctor.chosen": {
        "en": "(chosen on the dashboard)",
        "es": "(elegido en el panel)",
        "ca": "(triat al tauler)",
    },
    "cli.doctor.models": {
        "en": "Default model: {default}{chosen}; for summaries: {fast}{chosen_fast}.",
        "es": "Modelo por defecto: {default}{chosen}; para los resúmenes: {fast}{chosen_fast}.",
        "ca": "Model per defecte: {default}{chosen}; per als resums: {fast}{chosen_fast}.",
    },
    "cli.doctor.provider": {
        "en": "{label} (mode {mode}, model {model}): {detail}",
        "es": "{label} (modo {mode}, modelo {model}): {detail}",
        "ca": "{label} (mode {mode}, model {model}): {detail}",
    },
    "cli.doctor.provider_timeout": {
        "en": "{label} (mode {mode}): the provider is not responding.",
        "es": "{label} (modo {mode}): el proveedor no responde.",
        "ca": "{label} (mode {mode}): el proveïdor no respon.",
    },
    "cli.doctor.provider_failed": {
        "en": "{label} (mode {mode}): error while checking the status: {error}",
        "es": "{label} (modo {mode}): error al consultar el estado: {error}",
        "ca": "{label} (mode {mode}): error en consultar l'estat: {error}",
    },
    "cli.doctor.codex_state_dir": {
        "en": "{label} (mode cli): could not create a temporary directory for Codex's state "
        "({error}).",
        "es": "{label} (modo cli): no se ha podido crear un directorio temporal para el estado "
        "de Codex ({error}).",
        "ca": "{label} (mode cli): no s'ha pogut crear un directori temporal per a l'estat de "
        "Codex ({error}).",
    },
    "cli.doctor.critical_one": {
        "en": "There is {count} critical problem.",
        "es": "Hay {count} problema crítico.",
        "ca": "Hi ha {count} problema crític.",
    },
    "cli.doctor.critical_other": {
        "en": "There are {count} critical problems.",
        "es": "Hay {count} problemas críticos.",
        "ca": "Hi ha {count} problemes crítics.",
    },
    "cli.doctor.all_good": {"en": "All good.", "es": "Todo correcto.", "ca": "Tot correcte."},
    "cli.doctor.all_good_warnings_one": {
        "en": "All good ({count} warning).",
        "es": "Todo correcto ({count} aviso).",
        "ca": "Tot correcte ({count} avís).",
    },
    "cli.doctor.all_good_warnings_other": {
        "en": "All good ({count} warnings).",
        "es": "Todo correcto ({count} avisos).",
        "ca": "Tot correcte ({count} avisos).",
    },
    # -- reset-sessions, reset-throttle (admin.py) ------------------------------------
    "cli.sessions.none": {
        "en": "There was no open session.",
        "es": "No había ninguna sesión abierta.",
        "ca": "No hi havia cap sessió oberta.",
    },
    "cli.sessions.closed_one": {
        "en": "{count} session closed.",
        "es": "Se ha cerrado {count} sesión.",
        "ca": "S'ha tancat {count} sessió.",
    },
    "cli.sessions.closed_other": {
        "en": "{count} sessions closed.",
        "es": "Se han cerrado {count} sesiones.",
        "ca": "S'han tancat {count} sessions.",
    },
    "cli.sessions.log_in_again": {
        "en": "{sessions} You will need to log in again.",
        "es": "{sessions} Habrá que volver a iniciar sesión.",
        "ca": "{sessions} Caldrà tornar a iniciar sessió.",
    },
    "cli.sessions.devices_forgotten_one": {
        "en": "{count} known device forgotten.",
        "es": "Se ha olvidado {count} dispositivo conocido.",
        "ca": "S'ha oblidat {count} dispositiu conegut.",
    },
    "cli.sessions.devices_forgotten_other": {
        "en": "{count} known devices forgotten.",
        "es": "Se han olvidado {count} dispositivos conocidos.",
        "ca": "S'han oblidat {count} dispositius coneguts.",
    },
    "cli.throttle.none": {
        "en": "There was no lockout and no failed attempt on record.",
        "es": "No había ningún bloqueo ni ningún intento fallido registrado.",
        "ca": "No hi havia cap bloqueig ni cap intent fallit registrat.",
    },
    "cli.throttle.counters_one": {
        "en": "{count} failed-attempt counter",
        "es": "{count} contador de intentos fallidos",
        "ca": "{count} comptador d'intents fallits",
    },
    "cli.throttle.counters_other": {
        "en": "{count} failed-attempt counters",
        "es": "{count} contadores de intentos fallidos",
        "ca": "{count} comptadors d'intents fallits",
    },
    "cli.throttle.cleared": {
        "en": "Login lockouts cleared ({counters}).",
        "es": "Se han borrado los bloqueos de inicio de sesión ({counters}).",
        "ca": "S'han esborrat els bloquejos d'inici de sessió ({counters}).",
    },
    "cli.throttle.log_in": {
        "en": "Logging in works again from any address.",
        "es": "Ya se puede volver a iniciar sesión desde cualquier dirección.",
        "ca": "Ja es pot tornar a iniciar sessió des de qualsevol adreça.",
    },
    # -- init (admin.py) --------------------------------------------------------------
    "cli.init.account": {
        "en": "owner@{host}",
        "es": "propietario@{host}",
        "ca": "propietari@{host}",
    },
    "cli.init.account_without_host": {"en": "owner", "es": "propietario", "ca": "propietari"},
    "cli.init.owner_exists": {
        "en": "An owner is already set up. If you go on, the password and the TOTP will be "
        "replaced and every open session will be closed.",
        "es": "Ya hay un propietario configurado. Si continúas, se sustituirán la contraseña y "
        "el TOTP y se cerrarán todas las sesiones abiertas.",
        "ca": "Ja hi ha un propietari configurat. Si continues, se substituiran la contrasenya "
        "i el TOTP i es tancaran totes les sessions obertes.",
    },
    "cli.init.replace": {
        "en": "Replace it? [y/N]",
        "es": "¿Quieres sustituirlo? [s/N]",
        "ca": "Vols substituir-lo? [s/N]",
    },
    "cli.init.cancelled_unchanged": {
        "en": "Cancelled. Nothing was changed.",
        "es": "Operación cancelada. No se ha cambiado nada.",
        "ca": "Operació cancel·lada. No s'ha canviat res.",
    },
    "cli.init.cancelled_unsaved": {
        "en": "Cancelled. Nothing was saved.",
        "es": "Operación cancelada. No se ha guardado nada.",
        "ca": "Operació cancel·lada. No s'ha desat res.",
    },
    "cli.init.choose_password": {
        "en": "Choose the owner's password (at least {min} characters; a passphrase is better).",
        "es": "Elige la contraseña del propietario (mínimo {min} caracteres; mejor una frase "
        "de contraseña).",
        "ca": "Tria la contrasenya del propietari (mínim {min} caràcters; millor una frase de "
        "pas).",
    },
    "cli.init.new_password": {
        "en": "New password:",
        "es": "Nueva contraseña:",
        "ca": "Contrasenya nova:",
    },
    "cli.init.repeat_password": {
        "en": "Repeat the password:",
        "es": "Repite la contraseña:",
        "ca": "Repeteix la contrasenya:",
    },
    "cli.init.passwords_differ": {
        "en": "The passwords do not match.",
        "es": "Las contraseñas no coinciden.",
        "ca": "Les contrasenyes no coincideixen.",
    },
    "cli.init.too_many_attempts": {
        "en": "Too many attempts. Nothing was saved.",
        "es": "Demasiados intentos. No se ha guardado nada.",
        "ca": "Massa intents. No s'ha desat res.",
    },
    "cli.init.scan": {
        "en": "Scan this QR code with the authenticator app (Aegis, Google\n"
        "Authenticator, 1Password...):",
        "es": "Escanea este código QR con la aplicación de autenticación (Aegis, Google\n"
        "Authenticator, 1Password...):",
        "ca": "Escaneja aquest codi QR amb l'aplicació d'autenticació (Aegis, Google\n"
        "Authenticator, 1Password...):",
    },
    "cli.init.manual_key": {
        "en": "If you cannot scan it, enter this key by hand: {secret}",
        "es": "Si no puedes escanearlo, introduce esta clave a mano: {secret}",
        "ca": "Si no el pots escanejar, introdueix aquesta clau manualment: {secret}",
    },
    "cli.init.totp_code": {
        "en": "6-digit code the app shows:",
        "es": "Código de 6 cifras que muestra la aplicación:",
        "ca": "Codi de 6 xifres que mostra l'aplicació:",
    },
    "cli.init.wrong_code": {
        "en": "Wrong code. Check that the phone's clock and the server's are right.",
        "es": "Código incorrecto. Comprueba que la hora del teléfono y la del servidor son "
        "correctas.",
        "ca": "Codi incorrecte. Comprova que l'hora del telèfon i del servidor són correctes.",
    },
    "cli.init.totp_unconfirmed": {
        "en": "Could not confirm the TOTP. Nothing was saved.",
        "es": "No se ha podido confirmar el TOTP. No se ha guardado nada.",
        "ca": "No s'ha pogut confirmar el TOTP. No s'ha desat res.",
    },
    "cli.init.replaced": {
        "en": "Owner replaced. {sessions} The known devices were forgotten and the login "
        "lockouts cleared.",
        "es": "Propietario sustituido. {sessions} Se han olvidado los dispositivos conocidos y "
        "se han borrado los bloqueos de inicio de sesión.",
        "ca": "Propietari substituït. {sessions} S'han oblidat els dispositius coneguts i "
        "s'han esborrat els bloquejos d'inici de sessió.",
    },
    "cli.init.done": {
        "en": "Owner set up.",
        "es": "Propietario configurado.",
        "ca": "Propietari configurat.",
    },
    "cli.init.log_in": {
        "en": "You can now log in from the browser with the password and the TOTP code.",
        "es": "Ya puedes iniciar sesión en el navegador con la contraseña y el código TOTP.",
        "ca": "Ja pots iniciar sessió al navegador amb la contrasenya i el codi TOTP.",
    },
}
