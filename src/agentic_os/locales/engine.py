"""Texts of orchestrator/ and domain.py: the turn engine's errors, reasons and titles
(keys ``engine.*``)."""

from __future__ import annotations

from typing import Final

from agentic_os.i18n import Text

MESSAGES: Final[dict[str, Text]] = {
    "engine.default_title": {
        "en": "New conversation",
        "es": "Nueva conversación",
        "ca": "Conversa nova",
    },
    # -- the request (Engine._validate) -------------------------------------------------
    "engine.request.question_empty": {
        "en": "The question is empty.",
        "es": "La pregunta está vacía.",
        "ca": "La pregunta és buida.",
    },
    "engine.request.question_too_long": {
        "en": "The question is too long (at most {max} characters).",
        "es": "La pregunta es demasiado larga (máximo {max} caracteres).",
        "ca": "La pregunta és massa llarga (màxim {max} caràcters).",
    },
    "engine.request.mode": {
        "en": "Unknown turn mode.",
        "es": "Modo de turno desconocido.",
        "ca": "Mode de torn desconegut.",
    },
    "engine.request.agent": {
        "en": "Unknown agent.",
        "es": "Agente desconocido.",
        "ca": "Agent desconegut.",
    },
    "engine.request.debate_rounds": {
        "en": "Debate rounds must be between 0 and {max}.",
        "es": "Las rondas de debate deben estar entre 0 y {max}.",
        "ca": "Les rondes de debat han de ser entre 0 i {max}.",
    },
    "engine.request.consensus_threshold": {
        "en": "The consensus threshold must be between 0 and 100.",
        "es": "El umbral de consenso debe estar entre 0 y 100.",
        "ca": "El llindar de consens ha de ser entre 0 i 100.",
    },
    "engine.request.synthesizer": {
        "en": "Unknown synthesizer agent.",
        "es": "Agente sintetizador desconocido.",
        "ca": "Agent sintetitzador desconegut.",
    },
    "engine.request.model_id": {
        "en": "Invalid model identifier.",
        "es": "Identificador de modelo no válido.",
        "ca": "Identificador de model invàlid.",
    },
    "engine.request.too_many_attachments": {
        "en": "A message can have at most {max} attachments.",
        "es": "Un mensaje puede llevar como máximo {max} adjuntos.",
        "ca": "Un missatge pot portar com a màxim {max} adjunts.",
    },
    "engine.request.repeated_attachment": {
        "en": "The same attachment cannot appear twice in a message.",
        "es": "Un mismo adjunto no puede ir dos veces en el mensaje.",
        "ca": "Un mateix adjunt no pot anar dues vegades al missatge.",
    },
    "engine.request.pdf_in_revisions": {
        "en": "Unknown option for the PDFs of the reviews.",
        "es": "Opción desconocida para los PDF de las revisiones.",
        "ca": "Opció desconeguda per als PDF de les revisions.",
    },
    "engine.request.not_configured": {
        "en": "{agent} is not configured.",
        "es": "{agent} no está configurado.",
        "ca": "{agent} no està configurat.",
    },
    "engine.request.refine_rounds": {
        "en": "Refine rounds must be between {low} and {high}.",
        "es": "Las rondas de «Perfecciona» deben estar entre {low} y {high}.",
        "ca": "Les rondes de «Perfecciona» han de ser entre {low} i {high}.",
    },
    "engine.request.refine_words": {
        "en": "The word limit must be between {low} and {high}.",
        "es": "El límite de palabras debe estar entre {low} y {high}.",
        "ca": "El límit de paraules ha de ser entre {low} i {high}.",
    },
    "engine.request.refine_threshold": {
        "en": "The convergence threshold must be between {low} and {high}.",
        "es": "El umbral de convergencia debe estar entre {low} y {high}.",
        "ca": "El llindar de convergència ha de ser entre {low} i {high}.",
    },
    "engine.request.refine_editor": {
        "en": "Unknown editor agent.",
        "es": "Agente editor desconocido.",
        "ca": "Agent editor desconegut.",
    },
    "engine.request.refine_budget": {
        "en": "The Refine budget must be positive.",
        "es": "El presupuesto de «Perfecciona» debe ser positivo.",
        "ca": "El pressupost de «Perfecciona» ha de ser positiu.",
    },
    # -- the turn's failures -----------------------------------------------------------
    "engine.error.internal_turn": {
        "en": "An internal error occurred and the turn stopped.",
        "es": "Se ha producido un error interno y el turno se ha detenido.",
        "ca": "S'ha produït un error intern i el torn s'ha aturat.",
    },
    "engine.error.internal": {
        "en": "An internal error occurred.",
        "es": "Se ha producido un error interno.",
        "ca": "S'ha produït un error intern.",
    },
    "engine.error.conversation_not_found": {
        "en": "The conversation does not exist.",
        "es": "La conversación no existe.",
        "ca": "La conversa no existeix.",
    },
    "engine.error.attachment_not_found": {
        "en": "Attachment {id} does not exist.",
        "es": "El adjunto {id} no existe.",
        "ca": "L'adjunt {id} no existeix.",
    },
    "engine.error.attachments_too_large": {
        "en": "The attachments of a message cannot add up to more than {size}.",
        "es": "Los adjuntos de un mensaje no pueden sumar más de {size}.",
        "ca": "Els adjunts d'un missatge no poden sumar més de {size}.",
    },
    "engine.error.agent_failed": {
        "en": "{agent} could not answer.",
        "es": "{agent} no ha podido responder.",
        "ca": "{agent} no ha pogut respondre.",
    },
    "engine.error.both_failed": {
        "en": "Neither agent could answer.",
        "es": "Ninguno de los dos agentes ha podido responder.",
        "ca": "Cap dels dos agents ha pogut respondre.",
    },
    "engine.error.provider_unexpected": {
        "en": "Unexpected provider error.",
        "es": "Error inesperado del proveedor.",
        "ca": "Error inesperat del proveïdor.",
    },
    "engine.error.no_result": {
        "en": "The provider returned no result.",
        "es": "El proveedor no ha devuelto ningún resultado.",
        "ca": "El proveïdor no ha retornat cap resultat.",
    },
    "engine.error.reply_interrupted": {
        "en": "The model's answer was interrupted.",
        "es": "La respuesta del modelo se ha interrumpido.",
        "ca": "La resposta del model s'ha interromput.",
    },
    "engine.error.declined": {
        "en": "{model} declined the request and passed it to another model.",
        "es": "{model} ha rechazado la petición y la ha pasado a otro modelo.",
        "ca": "{model} ha declinat la petició i l'ha passada a un altre model.",
    },
    "engine.error.empty_reply": {
        "en": "The model returned an empty answer.",
        "es": "El modelo ha devuelto una respuesta vacía.",
        "ca": "El model ha retornat una resposta buida.",
    },
    "engine.error.empty_reply_max_tokens": {
        "en": "The model reached its output limit before writing any answer.",
        "es": "El modelo ha agotado el límite de salida antes de escribir ninguna respuesta.",
        "ca": "El model ha esgotat el límit de sortida abans d'escriure cap resposta.",
    },
    "engine.error.empty_reply_content_filter": {
        "en": "The content filter stopped the answer before the model wrote anything.",
        "es": (
            "El filtro de contenido ha detenido la respuesta antes de que el modelo "
            "escribiera nada."
        ),
        "ca": "El filtre de contingut ha aturat la resposta abans que el model escrivís res.",
    },
    "engine.error.empty_reply_interrupted": {
        "en": "The model's answer was interrupted before it wrote anything.",
        "es": "La respuesta del modelo se ha interrumpido antes de escribir nada.",
        "ca": "La resposta del model s'ha interromput abans d'escriure res.",
    },
    # -- refine turns (docs/adr/0010-refine-mode.md) -------------------------------------
    # Why a round wrote no new version, by its code (``reason_code``, domain.RefineReasonCode).
    "engine.refine.reason.over_budget": {
        "en": "The new version went over the word limit.",
        "es": "La nueva versión superaba el límite de palabras.",
        "ca": "La nova versió passava del límit de paraules.",
    },
    "engine.refine.reason.incomplete": {
        "en": "The editor wrote no complete version.",
        "es": "El editor no ha escrito ninguna versión completa.",
        "ca": "L'editor no ha escrit cap versió completa.",
    },
    "engine.refine.reason.identical": {
        "en": "The new version is the same as the previous one.",
        "es": "La nueva versión es igual a la anterior.",
        "ca": "La nova versió és igual a l'anterior.",
    },
    "engine.refine.reason.nothing_to_change": {
        "en": "Neither of them found anything to change.",
        "es": "Ninguno de los dos ha encontrado nada que cambiar.",
        "ca": "Cap dels dos hi ha trobat res a canviar.",
    },
    "engine.refine.reason.failed_round": {
        "en": "The models failed and the round wrote no version.",
        "es": "Los modelos han fallado y la ronda no ha escrito ninguna versión.",
        "ca": "Els models han fallat i la ronda no ha escrit cap versió.",
    },
    "engine.refine.no_changes": {
        "en": "The review does not have the list of changes it was asked for.",
        "es": "La revisión no tiene la lista de cambios que se le pedía.",
        "ca": "La revisió no té la llista de canvis que se li demanava.",
    },
    # -- the conversation's summary (memory.py) -----------------------------------------
    "engine.summary.empty": {
        "en": "The summary is empty.",
        "es": "El resumen está vacío.",
        "ca": "El resum és buit.",
    },
    "engine.summary.cut_off": {
        "en": "The summary was cut off.",
        "es": "El resumen se ha quedado cortado.",
        "ca": "El resum ha quedat tallat.",
    },
    # -- what a turn saved (accounting.py), stored with each saving ---------------------
    # (English and Spanish as «label: count», right for any count.)
    "engine.saving.cache": {
        "en": "Answer served from the cache",
        "es": "Respuesta servida desde la caché",
        "ca": "Resposta servida des de la memòria cau",
    },
    "engine.saving.compaction": {
        "en": "Tokens saved per call: {tokens}; calls: {calls}",
        "es": "Tokens ahorrados por llamada: {tokens}; llamadas: {calls}",
        "ca": "{tokens} tokens menys en {calls} crides",
    },
    "engine.saving.early_stop": {
        "en": "Rounds skipped by consensus: {rounds}",
        "es": "Rondas omitidas por consenso: {rounds}",
        "ca": "{rounds} rondes omeses per consens",
    },
    "engine.saving.unchanged": {
        "en": "Answers not rewritten: {count}",
        "es": "Respuestas sin reescribir: {count}",
        "ca": "{count} respostes sense reescriure",
    },
    # -- Claude's check of a PDF for ChatGPT (pdf_check.py) -------------------------------
    # The client writes these after a colon, with the first letter in lower case unless it
    # starts a name (Claude): each is a sentence of its own, and only «failed» has a colon,
    # before its detail.
    "engine.pdf_check.no_claude": {
        "en": "Claude is not available to check it.",
        "es": "Claude no está disponible para contrastarlo.",
        "ca": "Claude no està disponible per contrastar-lo.",
    },
    "engine.pdf_check.demo": {
        "en": "Claude is in demo mode and cannot check it.",
        "es": "Claude está en modo de demostración y no puede contrastarlo.",
        "ca": "Claude està en mode de demostració i no el pot contrastar.",
    },
    "engine.pdf_check.failed": {
        "en": "Claude's check failed: {message}",
        "es": "La comprobación de Claude ha fallado: {message}",
        "ca": "La comprovació de Claude ha fallat: {message}",
    },
    "engine.pdf_check.refused": {
        "en": "Claude declined to check it.",
        "es": "Claude no ha querido contrastarlo.",
        "ca": "Claude no l'ha volgut contrastar.",
    },
    "engine.pdf_check.timed_out": {
        "en": "Claude's check took too long.",
        "es": "La comprobación de Claude ha tardado demasiado.",
        "ca": "La comprovació de Claude ha trigat massa.",
    },
    "engine.pdf_check.partial": {
        "en": "Claude could only check it up to page {page}.",
        "es": "Claude solo ha podido contrastarlo hasta la página {page}.",
        "ca": "Claude només l'ha pogut contrastar fins a la pàgina {page}.",
    },
    "engine.pdf_check.not_analysed": {
        "en": "The server could not analyse its pages.",
        "es": "El servidor no ha podido analizar sus páginas.",
        "ca": "El servidor no n'ha pogut analitzar les pàgines.",
    },
    "engine.pdf_check.bad_format": {
        "en": "Claude's answer was not in the format asked for.",
        "es": "La respuesta de Claude no tenía el formato pedido.",
        "ca": "La resposta de Claude no tenia el format demanat.",
    },
}
