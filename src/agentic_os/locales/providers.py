"""Texts of providers/ (but the demo answers): status details, model descriptions, errors
(keys ``providers.*``)."""

from __future__ import annotations

from typing import Final

from agentic_os.i18n import Text

MESSAGES: Final[dict[str, Text]] = {
    # -- shared by several providers ---------------------------------------------------
    "providers.attachment_unreadable": {
        "en": "Could not read the attachment “{name}”.",
        "es": "No se ha podido leer el adjunto «{name}».",
        "ca": "No s'ha pogut llegir l'adjunt «{name}».",
    },
    "providers.attachment_changed": {
        "en": "The attachment “{name}” has changed since it was uploaded.",
        "es": "El adjunto «{name}» ha cambiado desde que se subió.",
        "ca": "L'adjunt «{name}» ha canviat des que es va pujar.",
    },
    "providers.no_session": {
        "en": "Not logged in: {hint}",
        "es": "Sin sesión: {hint}",
        "ca": "Sense sessió: {hint}",
    },
    "providers.api_key_configured": {
        "en": "API key configured",
        "es": "Clave de API configurada",
        "ca": "Clau d'API configurada",
    },
    "providers.model_configured": {
        "en": "Model set on the server.",
        "es": "Modelo configurado en el servidor.",
        "ca": "Model configurat al servidor.",
    },
    "providers.model.gpt_6_astra": {
        "en": "The most capable, for the most demanding work.",
        "es": "El más capaz, para el trabajo más exigente.",
        "ca": "El més capaç, per a la feina més exigent.",
    },
    "providers.model.gpt_6_sol": {
        "en": "Balanced, for everyday work.",
        "es": "Equilibrado, para el trabajo de cada día.",
        "ca": "Equilibrat, per a la feina de cada dia.",
    },
    "providers.model.gpt_6_luna": {
        "en": "Fast and cheap, for simple tasks.",
        "es": "Rápido y económico, para tareas sencillas.",
        "ca": "Ràpid i econòmic, per a tasques senzilles.",
    },
    # -- Claude (claude_cli.py, and claude_api.py for what both say) ---------------------
    "providers.claude.family.opus": {
        "en": "Deep reasoning and long tasks.",
        "es": "Razonamiento profundo y tareas largas.",
        "ca": "Raonament profund i tasques llargues.",
    },
    "providers.claude.family.sonnet": {
        "en": "A balance of quality and speed.",
        "es": "Equilibrio entre calidad y velocidad.",
        "ca": "Equilibri entre qualitat i velocitat.",
    },
    "providers.claude.family.haiku": {
        "en": "The fastest and cheapest; good for summaries.",
        "es": "El más rápido y económico; bueno para los resúmenes.",
        "ca": "El més ràpid i econòmic; bo per als resums.",
    },
    "providers.claude.family.fable": {
        "en": "The most capable; may not be included in every plan.",
        "es": "El más capaz; puede no estar incluido en todos los planes.",
        "ca": "El més capaç; pot no estar inclòs en tots els plans.",
    },
    "providers.claude.alias": {
        "en": "Always the newest version. {family}",
        "es": "Siempre la versión más reciente. {family}",
        "ca": "Sempre la versió més nova. {family}",
    },
    "providers.claude.alias_now": {
        "en": "Always the newest version. {family} Now: {model}",
        "es": "Siempre la versión más reciente. {family} Ahora: {model}",
        "ca": "Sempre la versió més nova. {family} Ara: {model}",
    },
    "providers.claude.login_hint": {
        "en": (
            "put the token from “claude setup-token” in CLAUDE_CODE_OAUTH_TOKEN (.env) and run "
            "“docker compose up -d”, or run “claude auth login” on the server"
        ),
        "es": (
            "pon el token de «claude setup-token» en CLAUDE_CODE_OAUTH_TOKEN (.env) y ejecuta "
            "«docker compose up -d», o ejecuta «claude auth login» en el servidor"
        ),
        "ca": (
            "posa el token de «claude setup-token» a CLAUDE_CODE_OAUTH_TOKEN (.env) i fes "
            "«docker compose up -d», o executa «claude auth login» al servidor"
        ),
    },
    "providers.claude.refused": {
        "en": "Claude declined to answer this request.",
        "es": "Claude se ha negado a responder a esta petición.",
        "ca": "Claude ha declinat respondre aquesta petició.",
    },
    "providers.claude.refused_category": {
        "en": "Claude declined to answer this request (category: {category}).",
        "es": "Claude se ha negado a responder a esta petición (categoría: {category}).",
        "ca": "Claude ha declinat respondre aquesta petició (categoria: {category}).",
    },
    "providers.claude.timeout": {
        "en": "Claude did not answer in time ({seconds} s).",
        "es": "Claude no ha respondido a tiempo ({seconds} s).",
        "ca": "Claude no ha respost a temps ({seconds} s).",
    },
    "providers.claude.closing": {
        "en": "The Claude provider is shutting down.",
        "es": "El proveedor de Claude se está deteniendo.",
        "ca": "El proveïdor de Claude s'està aturant.",
    },
    "providers.claude.no_result": {
        "en": "The Claude CLI sent no result.",
        "es": "La CLI de Claude no ha enviado ningún resultado.",
        "ca": "La CLI de Claude no ha enviat cap resultat.",
    },
    "providers.claude.system_prompt_too_long": {
        "en": "The system prompt is too long for the Claude CLI.",
        "es": "El prompt de sistema es demasiado largo para la CLI de Claude.",
        "ca": "El prompt de sistema és massa llarg per a la CLI de Claude.",
    },
    "providers.claude.sandbox_failed": {
        "en": "Could not prepare Claude's working directory ({reason}).",
        "es": "No se ha podido preparar el directorio de trabajo de Claude ({reason}).",
        "ca": "No s'ha pogut preparar el directori de treball de Claude ({reason}).",
    },
    "providers.claude.cli_not_found": {
        "en": "Claude CLI not found: check AOS_CLAUDE_CLI_PATH.",
        "es": "No se encuentra la CLI de Claude: revisa AOS_CLAUDE_CLI_PATH.",
        "ca": "CLI de Claude no trobada: revisa AOS_CLAUDE_CLI_PATH.",
    },
    "providers.claude.cli_not_executable": {
        "en": "The Claude CLI cannot be run (permissions).",
        "es": "No se puede ejecutar la CLI de Claude (permisos).",
        "ca": "No es pot executar la CLI de Claude (permisos).",
    },
    "providers.claude.event_too_large": {
        "en": "The Claude CLI sent an event that is too large.",
        "es": "La CLI de Claude ha enviado un evento demasiado grande.",
        "ca": "La CLI de Claude ha enviat un esdeveniment massa gran.",
    },
    "providers.claude.session_invalid": {
        "en": "The Claude session is not valid: {hint}.",
        "es": "La sesión de Claude no es válida: {hint}.",
        "ca": "La sessió de Claude no és vàlida: {hint}.",
    },
    "providers.claude.usage_limit": {
        "en": "The Claude subscription has reached its usage limit.",
        "es": "Se ha alcanzado el límite de uso de la suscripción de Claude.",
        "ca": "S'ha arribat al límit d'ús de la subscripció de Claude.",
    },
    "providers.claude.usage_limit_resets": {
        "en": (
            "The Claude subscription has reached its usage limit. "
            "It resets on {date} at {time} UTC."
        ),
        "es": (
            "Se ha alcanzado el límite de uso de la suscripción de Claude. "
            "Se restablece el {date} a las {time} UTC."
        ),
        "ca": (
            "S'ha arribat al límit d'ús de la subscripció de Claude. "
            "Es restableix el {date} a les {time} UTC."
        ),
    },
    "providers.claude.unavailable": {
        "en": "Claude is not available right now.",
        "es": "Claude no está disponible ahora mismo.",
        "ca": "Claude no està disponible ara mateix.",
    },
    "providers.claude.conversation_too_long": {
        "en": "The conversation is too long for Claude.",
        "es": "La conversación es demasiado larga para Claude.",
        "ca": "La conversa és massa llarga per a Claude.",
    },
    "providers.claude.cli_error": {
        "en": "The Claude CLI returned an error.",
        "es": "La CLI de Claude ha devuelto un error.",
        "ca": "La CLI de Claude ha retornat un error.",
    },
    # Followed by ": <the end of its stderr>" or ".".
    "providers.claude.stopped": {
        "en": "The Claude CLI stopped without answering",
        "es": "La CLI de Claude se ha detenido sin responder",
        "ca": "La CLI de Claude s'ha aturat sense respondre",
    },
    "providers.claude.stopped_code": {
        "en": "The Claude CLI stopped without answering (code {code})",
        "es": "La CLI de Claude se ha detenido sin responder (código {code})",
        "ca": "La CLI de Claude s'ha aturat sense respondre (codi {code})",
    },
    "providers.claude.status.cli_not_found": {
        "en": "Claude CLI not found",
        "es": "No se encuentra la CLI de Claude",
        "ca": "CLI de Claude no trobada",
    },
    "providers.claude.status.not_responding": {
        "en": "The Claude CLI is not responding",
        "es": "La CLI de Claude no responde",
        "ca": "La CLI de Claude no respon",
    },
    "providers.claude.status.unreadable": {
        "en": "Could not read the session state of the Claude CLI",
        "es": "No se ha podido leer el estado de la sesión de la CLI de Claude",
        "ca": "No s'ha pogut llegir l'estat de la sessió de la CLI de Claude",
    },
    "providers.claude.status.subscription": {
        "en": "Subscription active",
        "es": "Suscripción activa",
        "ca": "Subscripció activa",
    },
    "providers.claude.status.subscription_plan": {
        "en": "Subscription active ({plan})",
        "es": "Suscripción activa ({plan})",
        "ca": "Subscripció activa ({plan})",
    },
    "providers.claude.status.subscription_token": {
        "en": "Subscription active (OAuth token)",
        "es": "Suscripción activa (token OAuth)",
        "ca": "Subscripció activa (token OAuth)",
    },
    "providers.claude.status.api_key": {
        "en": "Logged in with an API key: billed per use, not through the subscription",
        "es": "Sesión con clave de API: se factura por uso, no con la suscripción",
        "ca": "Sessió amb clau d'API: es factura per ús, no amb la subscripció",
    },
    "providers.claude.status.session": {
        "en": "Session active",
        "es": "Sesión activa",
        "ca": "Sessió activa",
    },
    # -- Claude through the Anthropic API (claude_api.py) ---------------------------------
    "providers.claude.interrupted": {
        "en": "Claude's answer was interrupted.",
        "es": "La respuesta de Claude se ha interrumpido.",
        "ca": "La resposta de Claude s'ha interromput.",
    },
    "providers.claude.output_budget_spent": {
        "en": (
            "Claude used up its output limit of {budget} tokens (reasoning included) "
            "before writing any answer."
        ),
        "es": (
            "Claude ha agotado el límite de salida de {budget} tokens (razonamiento incluido) "
            "antes de escribir ninguna respuesta."
        ),
        "ca": (
            "Claude ha esgotat el límit de sortida de {budget} tokens (raonament inclòs) "
            "abans d'escriure cap resposta."
        ),
    },
    "providers.claude.stopped_before_text": {
        "en": "Claude's answer stopped before writing anything ({reason}).",
        "es": "La respuesta de Claude se ha detenido antes de escribir nada ({reason}).",
        "ca": "La resposta de Claude s'ha aturat abans d'escriure res ({reason}).",
    },
    "providers.claude.unexpected_message": {
        "en": "Unexpected message for the attachments.",
        "es": "Mensaje inesperado para los adjuntos.",
        "ca": "Missatge inesperat per als adjunts.",
    },
    "providers.anthropic.unreachable": {
        "en": "Could not reach Anthropic's API.",
        "es": "No se ha podido contactar con la API de Anthropic.",
        "ca": "No s'ha pogut contactar amb l'API d'Anthropic.",
    },
    "providers.anthropic.unexpected": {
        "en": "Unexpected response from Anthropic's API.",
        "es": "Respuesta inesperada de la API de Anthropic.",
        "ca": "Resposta inesperada de l'API d'Anthropic.",
    },
    "providers.anthropic.rate_limit": {
        "en": "Anthropic's API rate limit was reached.",
        "es": "Límite de peticiones de la API de Anthropic.",
        "ca": "Límit de peticions de l'API d'Anthropic.",
    },
    "providers.anthropic.key_rejected": {
        "en": "Anthropic's API rejected the API key (AOS_ANTHROPIC_API_KEY).",
        "es": "La API de Anthropic ha rechazado la clave de API (AOS_ANTHROPIC_API_KEY).",
        "ca": "L'API d'Anthropic ha rebutjat la clau d'API (AOS_ANTHROPIC_API_KEY).",
    },
    "providers.anthropic.billing": {
        "en": "Billing problem with the Anthropic account.",
        "es": "Problema de facturación de la cuenta de Anthropic.",
        "ca": "Problema de facturació del compte d'Anthropic.",
    },
    "providers.anthropic.unavailable": {
        "en": "Anthropic's API is not available right now.",
        "es": "La API de Anthropic no está disponible ahora mismo.",
        "ca": "L'API d'Anthropic no està disponible ara mateix.",
    },
    "providers.anthropic.rejected": {
        "en": "Anthropic's API rejected the request: {detail}",
        "es": "La API de Anthropic ha rechazado la petición: {detail}",
        "ca": "L'API d'Anthropic ha rebutjat la petició: {detail}",
    },
    "providers.anthropic.error": {
        "en": "Error from Anthropic's API ({status}).",
        "es": "Error de la API de Anthropic ({status}).",
        "ca": "Error de l'API d'Anthropic ({status}).",
    },
    "providers.anthropic.missing_key": {
        "en": "The Anthropic API key is missing (AOS_ANTHROPIC_API_KEY).",
        "es": "Falta la clave de API de Anthropic (AOS_ANTHROPIC_API_KEY).",
        "ca": "Falta la clau d'API d'Anthropic (AOS_ANTHROPIC_API_KEY).",
    },
    "providers.anthropic.status.missing_key": {
        "en": "The Anthropic API key is missing (AOS_ANTHROPIC_API_KEY)",
        "es": "Falta la clave de API de Anthropic (AOS_ANTHROPIC_API_KEY)",
        "ca": "Falta la clau d'API d'Anthropic (AOS_ANTHROPIC_API_KEY)",
    },
    # -- ChatGPT (openai_api.py, and codex_appserver.py for what both say) ----------------
    "providers.chatgpt.interrupted": {
        "en": "ChatGPT's answer was interrupted.",
        "es": "La respuesta de ChatGPT se ha interrumpido.",
        "ca": "La resposta de ChatGPT s'ha interromput.",
    },
    "providers.chatgpt.refused": {
        "en": "ChatGPT declined to answer this request.",
        "es": "ChatGPT se ha negado a responder a esta petición.",
        "ca": "ChatGPT ha declinat respondre aquesta petició.",
    },
    "providers.chatgpt.refused_explained": {
        "en": "ChatGPT declined to answer this request: “{explanation}”",
        "es": "ChatGPT se ha negado a responder a esta petición: «{explanation}»",
        "ca": "ChatGPT ha declinat respondre aquesta petició: «{explanation}»",
    },
    "providers.chatgpt.output_budget_spent": {
        "en": (
            "ChatGPT used up its output limit of {budget} tokens (reasoning included) "
            "before writing any answer."
        ),
        "es": (
            "ChatGPT ha agotado el límite de salida de {budget} tokens (razonamiento incluido) "
            "antes de escribir ninguna respuesta."
        ),
        "ca": (
            "ChatGPT ha esgotat el límit de sortida de {budget} tokens (raonament inclòs) "
            "abans d'escriure cap resposta."
        ),
    },
    "providers.chatgpt.content_filter": {
        "en": "OpenAI's content filter stopped the answer before ChatGPT wrote anything.",
        "es": (
            "El filtro de contenido de OpenAI ha detenido la respuesta antes de que ChatGPT "
            "escribiera nada."
        ),
        "ca": (
            "El filtre de contingut d'OpenAI ha aturat la resposta abans que ChatGPT escrivís res."
        ),
    },
    "providers.chatgpt.incomplete": {
        "en": "ChatGPT's answer was left incomplete before it wrote anything.",
        "es": "La respuesta de ChatGPT ha quedado incompleta antes de escribir nada.",
        "ca": "La resposta de ChatGPT ha quedat incompleta abans d'escriure res.",
    },
    "providers.chatgpt.timeout": {
        "en": "ChatGPT did not answer in time ({seconds} s).",
        "es": "ChatGPT no ha respondido a tiempo ({seconds} s).",
        "ca": "ChatGPT no ha respost a temps ({seconds} s).",
    },
    "providers.openai.rate_limit": {
        "en": "OpenAI's API rate limit was reached.",
        "es": "Límite de peticiones de la API de OpenAI.",
        "ca": "Límit de peticions de l'API d'OpenAI.",
    },
    "providers.openai.no_credit": {
        "en": "The OpenAI account has no credit available.",
        "es": "La cuenta de OpenAI no tiene crédito disponible.",
        "ca": "El compte d'OpenAI no té crèdit disponible.",
    },
    "providers.openai.rejected_code": {
        "en": "OpenAI's API rejected the request ({code}): {detail}",
        "es": "La API de OpenAI ha rechazado la petición ({code}): {detail}",
        "ca": "L'API d'OpenAI ha rebutjat la petició ({code}): {detail}",
    },
    "providers.openai.timeout": {
        "en": "OpenAI's API took too long to answer.",
        "es": "La API de OpenAI ha tardado demasiado en responder.",
        "ca": "L'API d'OpenAI ha trigat massa a respondre.",
    },
    "providers.openai.unreachable": {
        "en": "Could not reach OpenAI's API.",
        "es": "No se ha podido contactar con la API de OpenAI.",
        "ca": "No s'ha pogut contactar amb l'API d'OpenAI.",
    },
    "providers.openai.key_rejected": {
        "en": "OpenAI's API rejected the API key (AOS_OPENAI_API_KEY).",
        "es": "La API de OpenAI ha rechazado la clave de API (AOS_OPENAI_API_KEY).",
        "ca": "L'API d'OpenAI ha rebutjat la clau d'API (AOS_OPENAI_API_KEY).",
    },
    "providers.openai.unavailable": {
        "en": "OpenAI's API is not available right now.",
        "es": "La API de OpenAI no está disponible ahora mismo.",
        "ca": "L'API d'OpenAI no està disponible ara mateix.",
    },
    "providers.openai.rejected": {
        "en": "OpenAI's API rejected the request: {detail}",
        "es": "La API de OpenAI ha rechazado la petición: {detail}",
        "ca": "L'API d'OpenAI ha rebutjat la petició: {detail}",
    },
    "providers.openai.error": {
        "en": "Error from OpenAI's API ({status}).",
        "es": "Error de la API de OpenAI ({status}).",
        "ca": "Error de l'API d'OpenAI ({status}).",
    },
    "providers.openai.missing_key": {
        "en": "The OpenAI API key is missing (AOS_OPENAI_API_KEY).",
        "es": "Falta la clave de API de OpenAI (AOS_OPENAI_API_KEY).",
        "ca": "Falta la clau d'API d'OpenAI (AOS_OPENAI_API_KEY).",
    },
    "providers.openai.status.missing_key": {
        "en": "The OpenAI API key is missing (AOS_OPENAI_API_KEY)",
        "es": "Falta la clave de API de OpenAI (AOS_OPENAI_API_KEY)",
        "ca": "Falta la clau d'API d'OpenAI (AOS_OPENAI_API_KEY)",
    },
    # -- ChatGPT through Codex (codex_appserver.py) ---------------------------------------
    "providers.codex.login_hint": {
        "en": "run “codex login --device-auth” on the server",
        "es": "ejecuta «codex login --device-auth» en el servidor",
        "ca": "executa «codex login --device-auth» al servidor",
    },
    "providers.codex.too_many_subagents": {
        "en": "ChatGPT tried to open too many sub-agents; the answer was stopped.",
        "es": "ChatGPT ha intentado abrir demasiados subagentes; se ha detenido la respuesta.",
        "ca": "ChatGPT ha intentat obrir massa subagents; s'ha aturat la resposta.",
    },
    "providers.codex.usage_limit": {
        "en": "You have reached the usage limit of your ChatGPT subscription.",
        "es": "Has alcanzado el límite de uso de la suscripción de ChatGPT.",
        "ca": "Has arribat al límit d'ús de la subscripció de ChatGPT.",
    },
    "providers.codex.rate_limited": {
        "en": "ChatGPT is limiting requests; try again in a moment.",
        "es": "ChatGPT ha limitado las peticiones; vuelve a intentarlo dentro de poco.",
        "ca": "ChatGPT ha limitat les peticions; torna-ho a provar d'aquí a poc.",
    },
    "providers.codex.session_invalid": {
        "en": "The Codex session is not valid: {hint}.",
        "es": "La sesión de Codex no es válida: {hint}.",
        "ca": "La sessió de Codex no és vàlida: {hint}.",
    },
    "providers.codex.no_session": {
        "en": "Codex is not logged in: {hint}.",
        "es": "Codex no tiene sesión: {hint}.",
        "ca": "Codex no té sessió: {hint}.",
    },
    "providers.codex.conversation_too_long": {
        "en": "The conversation is too long for ChatGPT's model.",
        "es": "La conversación es demasiado larga para el modelo de ChatGPT.",
        "ca": "La conversa és massa llarga per al model de ChatGPT.",
    },
    "providers.codex.usage_policy": {
        "en": "ChatGPT rejected the request under its usage policy.",
        "es": "ChatGPT ha rechazado la petición por su política de uso.",
        "ca": "ChatGPT ha rebutjat la petició per la seva política d'ús.",
    },
    "providers.codex.rejected": {
        "en": "Codex rejected the request.",
        "es": "Codex ha rechazado la petición.",
        "ca": "Codex ha rebutjat la petició.",
    },
    "providers.codex.unavailable": {
        "en": "ChatGPT is not available right now.",
        "es": "ChatGPT no está disponible ahora mismo.",
        "ca": "ChatGPT no està disponible ara mateix.",
    },
    "providers.codex.failed": {
        "en": "Codex failed.",
        "es": "Codex ha fallado.",
        "ca": "Codex ha fallat.",
    },
    "providers.codex.server_error": {
        "en": "Codex server error.",
        "es": "Error del servidor de Codex.",
        "ca": "Error del servidor de Codex.",
    },
    "providers.codex.timeout": {
        "en": "ChatGPT (Codex) went over the maximum answer time.",
        "es": "ChatGPT (Codex) ha superado el tiempo máximo de respuesta.",
        "ca": "ChatGPT (Codex) ha superat el temps màxim de resposta.",
    },
    "providers.codex.process_stopped": {
        "en": "The Codex process stopped unexpectedly.",
        "es": "El proceso de Codex se ha detenido inesperadamente.",
        "ca": "El procés de Codex s'ha aturat inesperadament.",
    },
    "providers.codex.closed": {
        "en": "The Codex provider is closed.",
        "es": "El proveedor de Codex está cerrado.",
        "ca": "El proveïdor de Codex està tancat.",
    },
    "providers.codex.restarting": {
        "en": "Codex stopped; it will restart in {seconds} s.",
        "es": "Codex se ha detenido; se reiniciará dentro de {seconds} s.",
        "ca": "Codex s'ha aturat; es reiniciarà d'aquí a {seconds} s.",
    },
    "providers.codex.dirs_failed": {
        "en": "Could not prepare the Codex directories: {reason}",
        "es": "No se han podido preparar los directorios de Codex: {reason}",
        "ca": "No s'han pogut preparar els directoris de Codex: {reason}",
    },
    "providers.codex.cli_not_found": {
        "en": "Codex CLI not found: {path}",
        "es": "No se encuentra la CLI de Codex: {path}",
        "ca": "CLI de Codex no trobada: {path}",
    },
    "providers.codex.cli_failed": {
        "en": "Could not run the Codex CLI: {reason}",
        "es": "No se ha podido ejecutar la CLI de Codex: {reason}",
        "ca": "No s'ha pogut executar la CLI de Codex: {reason}",
    },
    "providers.codex.start_failed": {
        "en": "Could not start Codex.",
        "es": "No se ha podido iniciar Codex.",
        "ca": "No s'ha pogut iniciar Codex.",
    },
    "providers.codex.start_failed_detail": {
        "en": "Could not start Codex. ({detail})",
        "es": "No se ha podido iniciar Codex. ({detail})",
        "ca": "No s'ha pogut iniciar Codex. ({detail})",
    },
    "providers.codex.status.unreadable": {
        "en": "Could not read the state of Codex.",
        "es": "No se ha podido leer el estado de Codex.",
        "ca": "No s'ha pogut llegir l'estat de Codex.",
    },
    "providers.codex.status.own_provider": {
        "en": "Codex with its own model provider",
        "es": "Codex con un proveedor de modelos propio",
        "ca": "Codex amb un proveïdor de models propi",
    },
    "providers.codex.status.subscription": {
        "en": "ChatGPT subscription active",
        "es": "Suscripción de ChatGPT activa",
        "ca": "Subscripció ChatGPT activa",
    },
    "providers.codex.status.subscription_plan": {
        "en": "ChatGPT subscription active ({plan})",
        "es": "Suscripción de ChatGPT activa ({plan})",
        "ca": "Subscripció ChatGPT activa ({plan})",
    },
    "providers.codex.status.api_key": {
        "en": "Codex with an API key (billed per use)",
        "es": "Codex con clave de API (se factura por uso)",
        "ca": "Codex amb clau d'API (es factura per ús)",
    },
    "providers.codex.status.external_account": {
        "en": "Codex with an external account",
        "es": "Codex con una cuenta externa",
        "ca": "Codex amb un compte extern",
    },
    # -- the demo provider (fake.py; its answers are in locales/demo.py) ------------------
    "providers.fake.status": {
        "en": "Demo mode",
        "es": "Modo demo",
        "ca": "Mode demostració",
    },
    "providers.fake.label": {
        "en": "{agent} (demo)",
        "es": "{agent} (demo)",
        "ca": "{agent} (demostració)",
    },
    "providers.fake.label_mini": {
        "en": "{agent} mini (demo)",
        "es": "{agent} mini (demo)",
        "ca": "{agent} mini (demostració)",
    },
    "providers.fake.description": {
        "en": "Canned answers, with no real model and no cost.",
        "es": "Respuestas predefinidas, sin ningún modelo real ni coste.",
        "ca": "Respostes predefinides, sense cap model real ni cost.",
    },
    "providers.fake.description_mini": {
        "en": "Fast demo variant, the one used for summaries.",
        "es": "Variante rápida de demostración, la de los resúmenes.",
        "ca": "Variant ràpida de demostració, la dels resums.",
    },
    "providers.fake.failed": {
        "en": "{agent} (demo) failed on purpose.",
        "es": "{agent} (demo) ha fallado a propósito.",
        "ca": "{agent} (demostració) ha fallat a propòsit.",
    },
    "providers.fake.refused": {
        "en": "{agent} (demo) declined to answer this request.",
        "es": "{agent} (demo) se ha negado a responder a esta petición.",
        "ca": "{agent} (demostració) ha declinat respondre aquesta petició.",
    },
}
