"""Texts of server/ and security/: HTTP and WebSocket errors, login (keys ``server.*``)."""

from __future__ import annotations

from typing import Final

from agentic_os.i18n import Text

MESSAGES: Final[dict[str, Text]] = {
    "server.quoted": {"en": '"{text}"', "es": "«{text}»", "ca": "«{text}»"},
    "server.login_required": {
        "en": "You need to log in.",
        "es": "Tienes que iniciar sesión.",
        "ca": "Cal iniciar sessió.",
    },
    # -- HTTP errors raised without a detail of their own (app.py) --------------------
    "server.http.bad_request": {
        "en": "Bad request.",
        "es": "Petición incorrecta.",
        "ca": "Petició incorrecta.",
    },
    "server.http.forbidden": {
        "en": "Access denied.",
        "es": "Acceso denegado.",
        "ca": "Accés denegat.",
    },
    "server.http.not_found": {
        "en": "Not found.",
        "es": "No se ha encontrado.",
        "ca": "No s'ha trobat.",
    },
    "server.http.method_not_allowed": {
        "en": "Method not allowed.",
        "es": "Método no permitido.",
        "ca": "Mètode no permès.",
    },
    "server.http.too_large": {
        "en": "The request is too large.",
        "es": "La petición es demasiado grande.",
        "ca": "La petició és massa gran.",
    },
    "server.http.unsupported_media_type": {
        "en": "This file type is not supported.",
        "es": "Este tipo de archivo no se admite.",
        "ca": "Aquest tipus de fitxer no s'admet.",
    },
    "server.http.invalid_data": {
        "en": "Invalid data.",
        "es": "Datos no válidos.",
        "ca": "Dades no vàlides.",
    },
    "server.http.invalid_fields": {
        "en": "Invalid data: {fields}.",
        "es": "Datos no válidos: {fields}.",
        "ca": "Dades no vàlides: {fields}.",
    },
    "server.http.too_many_requests": {
        "en": "Too many attempts.",
        "es": "Demasiados intentos.",
        "ca": "Massa intents.",
    },
    "server.http.internal_error": {
        "en": "Internal server error.",
        "es": "Error interno del servidor.",
        "ca": "Error intern del servidor.",
    },
    "server.http.unavailable": {
        "en": "Service unavailable.",
        "es": "Servicio no disponible.",
        "ca": "Servei no disponible.",
    },
    # -- Requests (middleware.py, deps.py) --------------------------------------------
    "server.forbidden_origin": {
        "en": "Origin not allowed.",
        "es": "Origen no permitido.",
        "ca": "Origen no permès.",
    },
    "server.request_too_slow": {
        "en": "The request took too long to arrive. Try again.",
        "es": "La petición ha tardado demasiado en llegar. Vuelve a intentarlo.",
        "ca": "La petició ha trigat massa a arribar. Torna-ho a provar.",
    },
    "server.request_too_large": {
        "en": "The request is too large (maximum {limit}).",
        "es": "La petición es demasiado grande (máximo {limit}).",
        "ca": "La petició és massa gran (màxim {limit}).",
    },
    "server.client_disconnected": {
        "en": "The connection closed before the whole request arrived.",
        "es": "La conexión se ha cerrado antes de recibir la petición entera.",
        "ca": "La connexió s'ha tancat abans de rebre la petició sencera.",
    },
    "server.invalid_json": {
        "en": "The request body must be valid JSON.",
        "es": "El cuerpo de la petición debe ser JSON válido.",
        "ca": "El cos de la petició ha de ser JSON vàlid.",
    },
    "server.invalid_text": {
        "en": "The request contains text that is not valid UTF-8.",
        "es": "La petición contiene texto que no es UTF-8 válido.",
        "ca": "La petició conté text que no és UTF-8 vàlid.",
    },
    # -- Conversations and settings (routes_api.py) -----------------------------------
    "server.conversation_not_found": {
        "en": "The conversation does not exist.",
        "es": "La conversación no existe.",
        "ca": "La conversa no existeix.",
    },
    "server.title_required": {
        "en": "A title is required (as text).",
        "es": "Hay que indicar el título (texto).",
        "ca": "Cal indicar el títol (text).",
    },
    "server.number_too_large": {
        "en": "A number is too large.",
        "es": "Hay un número demasiado grande.",
        "ca": "Hi ha un nombre massa gran.",
    },
    "server.settings.revision_required": {
        "en": '"revision" is required (the revision of the settings the change is based on). '
        "Reload the page.",
        "es": "Hay que indicar «revision» (la revisión de la configuración en la que se basa "
        "el cambio). Vuelve a cargar la página.",
        "ca": "Cal indicar «revision» (la revisió de la configuració en què es basa el canvi). "
        "Torna a carregar la pàgina.",
    },
    "server.settings.conflict": {
        "en": "The settings have changed in another tab or on another device. Review them "
        "and save them again.",
        "es": "La configuración ha cambiado en otra pestaña o dispositivo. Revísala y vuelve "
        "a guardarla.",
        "ca": "La configuració ha canviat en una altra pestanya o dispositiu. Revisa-la i "
        "torna-la a desar.",
    },
    # -- Attachments (routes_attachments.py) ------------------------------------------
    "server.attachment.not_found": {
        "en": "The attachment does not exist.",
        "es": "El adjunto no existe.",
        "ca": "L'adjunt no existeix.",
    },
    "server.attachment.no_thumbnail": {
        "en": "This attachment has no thumbnail.",
        "es": "Este adjunto no tiene miniatura.",
        "ca": "Aquest adjunt no té miniatura.",
    },
    "server.attachment.in_use": {
        "en": "This attachment has already been sent in a conversation: it will be deleted "
        "when the conversation is deleted.",
        "es": "Este adjunto ya se ha enviado en una conversación: se borrará cuando se borre "
        "la conversación.",
        "ca": "Aquest adjunt ja s'ha enviat en una conversa: s'esborrarà quan s'esborri la "
        "conversa.",
    },
    "server.attachment.disk_full": {
        "en": "The server does not have enough disk space to save the file.",
        "es": "El servidor no tiene suficiente espacio en disco para guardar el archivo.",
        "ca": "El servidor no té prou espai al disc per desar el fitxer.",
    },
    # -- Login (routes_auth.py) -------------------------------------------------------
    "server.login.failed": {
        "en": "Incorrect credentials.",
        "es": "Credenciales incorrectas.",
        "ca": "Credencials incorrectes.",
    },
    "server.login.credentials_required": {
        "en": "The password and the TOTP code are required.",
        "es": "Hay que indicar la contraseña y el código TOTP.",
        "ca": "Cal indicar la contrasenya i el codi TOTP.",
    },
    "server.login.locked": {
        "en": "Too many failed attempts. Try again in {wait}.",
        "es": "Demasiados intentos fallidos. Vuelve a intentarlo dentro de {wait}.",
        "ca": "Massa intents fallits. Torna-ho a provar d'aquí a {wait}.",
    },
    "server.login.seconds_one": {
        "en": "{count} second",
        "es": "{count} segundo",
        "ca": "{count} segon",
    },
    "server.login.seconds_other": {
        "en": "{count} seconds",
        "es": "{count} segundos",
        "ca": "{count} segons",
    },
    "server.login.minutes_one": {
        "en": "{count} minute",
        "es": "{count} minuto",
        "ca": "{count} minut",
    },
    "server.login.minutes_other": {
        "en": "{count} minutes",
        "es": "{count} minutos",
        "ca": "{count} minuts",
    },
    # -- The owner's password (security/passwords.py) ---------------------------------
    "server.password.too_short": {
        "en": "The password must be at least {min} characters long (a passphrase is better).",
        "es": "La contraseña debe tener como mínimo {min} caracteres (mejor una frase de "
        "contraseña).",
        "ca": "La contrasenya ha de tenir com a mínim {min} caràcters (millor una frase de pas).",
    },
    "server.password.too_long": {
        "en": "The password cannot be longer than {max} characters.",
        "es": "La contraseña no puede tener más de {max} caracteres.",
        "ca": "La contrasenya no pot tenir més de {max} caràcters.",
    },
    "server.password.blank": {
        "en": "The password cannot be only spaces.",
        "es": "La contraseña no puede ser solo espacios.",
        "ca": "La contrasenya no pot ser només espais.",
    },
    # -- The page without a build of the web app (static.py) --------------------------
    "server.not_built.running": {
        "en": "The server is running, but it cannot find the built web interface.",
        "es": "El servidor funciona, pero no encuentra la interfaz web compilada.",
        "ca": "El servidor funciona, però no troba la interfície web compilada.",
    },
    "server.not_built.build": {
        "en": "Build it from the root of the project:",
        "es": "Compílala desde la raíz del proyecto:",
        "ca": "Compila-la des de l'arrel del projecte:",
    },
    "server.not_built.or_variable": {
        "en": "or say where it is with the variable {variable}. Then restart the server.",
        "es": "o indica dónde está con la variable {variable}. Después reinicia el servidor.",
        "ca": "o indica on és amb la variable {variable}. Després reinicia el servidor.",
    },
    # -- The agents' status (status.py) -----------------------------------------------
    "server.status.checking": {
        "en": "Checking the status…",
        "es": "Comprobando el estado…",
        "ca": "S'està comprovant l'estat…",
    },
    "server.status.timeout": {
        "en": "The provider is not responding.",
        "es": "El proveedor no responde.",
        "ca": "El proveïdor no respon.",
    },
    "server.status.failed": {
        "en": "Could not check the provider's status.",
        "es": "No se ha podido consultar el estado del proveedor.",
        "ca": "No s'ha pogut consultar l'estat del proveïdor.",
    },
    # -- Turns (turns.py) -------------------------------------------------------------
    "server.turn.stop_only_refine": {
        "en": "Only a Refine turn can stop after the current round; to stop it now, cancel it.",
        "es": "Solo un turno «Perfecciona» se puede parar al acabar la ronda; para pararlo "
        "ahora, cancélalo.",
        "ca": "Només un torn «Perfecciona» es pot aturar en acabar la ronda; per aturar-lo "
        "ara, cancel·la'l.",
    },
    "server.turn.shutting_down": {
        "en": "The server is shutting down.",
        "es": "El servidor se está deteniendo.",
        "ca": "El servidor s'està aturant.",
    },
    "server.turn.duplicate": {
        "en": "This request id has already been used.",
        "es": "Este identificador de petición ya se ha usado.",
        "ca": "Aquest identificador de petició ja s'ha fet servir.",
    },
    "server.turn.too_many": {
        "en": "There are already {count} turns running. Wait for one of them to finish.",
        "es": "Ya hay {count} turnos en curso. Espera a que termine alguno.",
        "ca": "Ja hi ha {count} torns en curs. Espera que n'acabi algun.",
    },
    "server.turn.conversation_busy": {
        "en": "This conversation already has a turn running.",
        "es": "Esta conversación ya tiene un turno en curso.",
        "ca": "Aquesta conversa ja té un torn en curs.",
    },
    "server.turn.internal_error": {
        "en": "An internal error occurred and the turn stopped.",
        "es": "Se ha producido un error interno y el turno se ha detenido.",
        "ca": "S'ha produït un error intern i el torn s'ha aturat.",
    },
    # -- WebSocket messages (ws.py) ---------------------------------------------------
    "server.ws.not_text": {
        "en": "Only JSON text messages are accepted.",
        "es": "Solo se aceptan mensajes de texto JSON.",
        "ca": "Només s'accepten missatges de text JSON.",
    },
    "server.ws.too_large": {
        "en": "The message is too large.",
        "es": "El mensaje es demasiado grande.",
        "ca": "El missatge és massa gran.",
    },
    "server.ws.invalid_json": {
        "en": "The message is not valid JSON.",
        "es": "El mensaje no es JSON válido.",
        "ca": "El missatge no és JSON vàlid.",
    },
    "server.ws.no_type": {
        "en": 'The message must be an object with a "type" field.',
        "es": "El mensaje debe ser un objeto con un campo «type».",
        "ca": "El missatge ha de ser un objecte amb un camp «type».",
    },
    "server.ws.invalid_text": {
        "en": "The message contains text that is not valid UTF-8.",
        "es": "El mensaje contiene texto que no es UTF-8 válido.",
        "ca": "El missatge conté text que no és UTF-8 vàlid.",
    },
    "server.ws.internal_error": {
        "en": "Internal error while processing the message.",
        "es": "Error interno al procesar el mensaje.",
        "ca": "Error intern en processar el missatge.",
    },
    "server.ws.unknown_type": {
        "en": 'Unknown message type: "{type}".',
        "es": "Tipo de mensaje desconocido: «{type}».",
        "ca": "Tipus de missatge desconegut: «{type}».",
    },
    "server.ws.bad_ping": {
        "en": '"t" must be a number.',
        "es": "«t» debe ser un número.",
        "ca": "«t» ha de ser un número.",
    },
    "server.ws.bad_request_id": {
        "en": '"request_id" must be a string of 1 to {max} characters.',
        "es": "«request_id» debe ser un texto de 1 a {max} caracteres.",
        "ca": "«request_id» ha de ser un text d'1 a {max} caràcters.",
    },
    "server.ws.bad_after_seq": {
        "en": '"after_seq" must be an integer ≥ 0.',
        "es": "«after_seq» debe ser un entero ≥ 0.",
        "ca": "«after_seq» ha de ser un enter ≥ 0.",
    },
    "server.ws.bad_text": {
        "en": '"text" must be a string.',
        "es": "«text» debe ser un texto.",
        "ca": "«text» ha de ser un text.",
    },
    "server.ws.bad_mode": {
        "en": '"mode" must be "solo", "duel", "debate" or "refine".',
        "es": "«mode» debe ser «solo», «duel», «debate» o «refine».",
        "ca": "«mode» ha de ser «solo», «duel», «debate» o «refine».",
    },
    "server.ws.bad_target": {
        "en": '"target" must be "claude" or "chatgpt".',
        "es": "«target» debe ser «claude» o «chatgpt».",
        "ca": "«target» ha de ser «claude» o «chatgpt».",
    },
    "server.ws.bad_conversation_id": {
        "en": '"conversation_id" must be a positive integer or null.',
        "es": "«conversation_id» debe ser un entero positivo o null.",
        "ca": "«conversation_id» ha de ser un enter positiu o null.",
    },
    "server.ws.bad_options": {
        "en": '"options" must be an object.',
        "es": "«options» debe ser un objeto.",
        "ca": "«options» ha de ser un objecte.",
    },
    "server.ws.bad_option": {
        "en": '"options.{name}" must be an object.',
        "es": "«options.{name}» debe ser un objeto.",
        "ca": "«options.{name}» ha de ser un objecte.",
    },
    "server.ws.bad_models": {
        "en": '"models" must be an object (agent → model).',
        "es": "«models» debe ser un objeto (agente → modelo).",
        "ca": "«models» ha de ser un objecte (agent → model).",
    },
    "server.ws.bad_models_agent": {
        "en": '"models" only accepts "claude" and "chatgpt".',
        "es": "«models» solo admite «claude» y «chatgpt».",
        "ca": "«models» només admet «claude» i «chatgpt».",
    },
    "server.ws.bad_attachments": {
        "en": '"attachments" must be a list of attachment ids (positive integers).',
        "es": "«attachments» debe ser una lista de identificadores de adjunto (enteros positivos).",
        "ca": "«attachments» ha de ser una llista d'identificadors d'adjunt (enters positius).",
    },
    "server.ws.too_many_attachments": {
        "en": "A message can carry at most {max} attachments.",
        "es": "Un mensaje puede llevar como máximo {max} adjuntos.",
        "ca": "Un missatge pot portar com a màxim {max} adjunts.",
    },
    "server.ws.repeated_attachment": {
        "en": "The same attachment cannot be in a message twice.",
        "es": "Un mismo adjunto no puede ir dos veces en el mensaje.",
        "ca": "Un mateix adjunt no pot anar dues vegades al missatge.",
    },
}
