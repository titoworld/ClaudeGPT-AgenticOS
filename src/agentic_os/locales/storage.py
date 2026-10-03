"""Texts of storage/: settings validation and the store's errors (keys ``storage.*``)."""

from __future__ import annotations

from typing import Final

from agentic_os.i18n import Text

MESSAGES: Final[dict[str, Text]] = {
    "storage.default_title": {
        "en": "New conversation",
        "es": "Nueva conversación",
        "ca": "Conversa nova",
    },
    "storage.conversation_not_found": {
        "en": "Conversation {id} does not exist.",
        "es": "La conversación {id} no existe.",
        "ca": "La conversa {id} no existeix.",
    },
    "storage.attachment_in_use": {
        "en": "Attachment {id} is already part of a conversation.",
        "es": "El adjunto {id} ya forma parte de una conversación.",
        "ca": "L'adjunt {id} ja forma part d'una conversa.",
    },
    "storage.title_empty": {
        "en": "The title cannot be empty.",
        "es": "El título no puede estar vacío.",
        "ca": "El títol no pot estar buit.",
    },
    "storage.title_too_long": {
        "en": "The title cannot be longer than {max} characters.",
        "es": "El título no puede tener más de {max} caracteres.",
        "ca": "El títol no pot tenir més de {max} caràcters.",
    },
    "storage.list_limit": {
        "en": '"limit" must be an integer between 1 and {max}.',
        "es": "«limit» debe ser un entero entre 1 y {max}.",
        "ca": "«limit» ha de ser un enter entre 1 i {max}.",
    },
    "storage.search_too_long": {
        "en": "The search cannot be longer than {max} characters.",
        "es": "La búsqueda no puede tener más de {max} caracteres.",
        "ca": "La cerca no pot tenir més de {max} caràcters.",
    },
    "storage.stats_days": {
        "en": '"days" must be an integer between 1 and {max}.',
        "es": "«days» debe ser un entero entre 1 y {max}.",
        "ca": "«days» ha de ser un enter entre 1 i {max}.",
    },
    "storage.newer_schema": {
        "en": "The database has schema version {found}, newer than version {known}, the "
        "one this version of the application knows. Update the application.",
        "es": "La base de datos tiene la versión de esquema {found}, más nueva que la "
        "{known} que conoce esta versión de la aplicación. Actualízala.",
        "ca": "La base de dades té la versió d'esquema {found}, més nova que la {known} que "
        "coneix aquesta versió de l'aplicació. Actualitza-la.",
    },
    # -- Settings validation (models.py, pricing.py) ----------------------------------
    "storage.settings.not_json_object": {
        "en": "The settings must be a JSON object.",
        "es": "La configuración debe ser un objeto JSON.",
        "ca": "La configuració ha de ser un objecte JSON.",
    },
    "storage.settings.not_object": {
        "en": '"{name}" must be an object.',
        "es": "«{name}» debe ser un objeto.",
        "ca": "«{name}» ha de ser un objecte.",
    },
    "storage.settings.field_error": {
        "en": '"{name}": {error}',
        "es": "«{name}»: {error}",
        "ca": "«{name}»: {error}",
    },
    "storage.settings.revision": {
        "en": '"revision" must be an integer greater than or equal to 0.',
        "es": "«revision» debe ser un entero mayor o igual que 0.",
        "ca": "«revision» ha de ser un enter igual o més gran que 0.",
    },
    "storage.settings.int_range": {
        "en": '"{name}" must be an integer between {low} and {high}.',
        "es": "«{name}» debe ser un entero entre {low} y {high}.",
        "ca": "«{name}» ha de ser un enter entre {low} i {high}.",
    },
    "storage.settings.number_range": {
        "en": '"{name}" must be a number between {low} and {high}.',
        "es": "«{name}» debe ser un número entre {low} y {high}.",
        "ca": "«{name}» ha de ser un nombre entre {low} i {high}.",
    },
    "storage.settings.bool": {
        "en": '"{name}" must be a boolean (true or false).',
        "es": "«{name}» debe ser un booleano (true o false).",
        "ca": "«{name}» ha de ser un booleà (true o false).",
    },
    "storage.settings.mode": {
        "en": '"{name}" must be "solo", "duel", "debate" or "refine".',
        "es": "«{name}» debe ser «solo», «duel», «debate» o «refine».",
        "ca": "«{name}» ha de ser «solo», «duel», «debate» o «refine».",
    },
    "storage.settings.default_mode_refine": {
        "en": 'The default mode cannot be "refine".',
        "es": "El modo por defecto no puede ser «refine».",
        "ca": "El mode per defecte no pot ser «refine».",
    },
    "storage.settings.agent": {
        "en": '"{name}" must be "claude" or "chatgpt".',
        "es": "«{name}» debe ser «claude» o «chatgpt».",
        "ca": "«{name}» ha de ser «claude» o «chatgpt».",
    },
    "storage.settings.agent_map": {
        "en": '"{name}" must be an object with the keys "claude" and "chatgpt".',
        "es": "«{name}» debe ser un objeto con las claves «claude» y «chatgpt».",
        "ca": "«{name}» ha de ser un objecte amb les claus «claude» i «chatgpt».",
    },
    "storage.settings.agent_map_keys": {
        "en": '"{name}" only accepts the keys "claude" and "chatgpt".',
        "es": "«{name}» solo admite las claves «claude» y «chatgpt».",
        "ca": "«{name}» només admet les claus «claude» i «chatgpt».",
    },
    "storage.settings.pdf_in_revisions": {
        "en": '"pdf_in_revisions" must be "full" or "text".',
        "es": "«pdf_in_revisions» debe ser «full» o «text».",
        "ca": "«pdf_in_revisions» ha de ser «full» o «text».",
    },
    "storage.settings.max_words": {
        "en": '"refine.max_words" must be null (automatic) or an integer between {low} and {high}.',
        "es": "«refine.max_words» debe ser null (automático) o un entero entre {low} y {high}.",
        "ca": "«refine.max_words» ha de ser null (automàtic) o un enter entre {low} i {high}.",
    },
    "storage.settings.model_id": {
        "en": '"{name}" must be a valid model id: up to 100 letters, digits or the signs '
        ". _ : / @ [ ] -, without spaces.",
        "es": "«{name}» debe ser un identificador de modelo válido: hasta 100 letras, cifras "
        "o los signos . _ : / @ [ ] -, sin espacios.",
        "ca": "«{name}» ha de ser un identificador de model vàlid: fins a 100 lletres, xifres "
        "o els signes . _ : / @ [ ] -, sense espais.",
    },
    "storage.settings.fx_mode": {
        "en": '"fx.mode" must be "auto" or "manual".',
        "es": "«fx.mode» debe ser «auto» o «manual».",
        "ca": "«fx.mode» ha de ser «auto» o «manual».",
    },
    "storage.settings.prices_object": {
        "en": '"prices" must be an object (model → prices).',
        "es": "«prices» debe ser un objeto (modelo → precios).",
        "ca": "«prices» ha de ser un objecte (model → preus).",
    },
    "storage.settings.prices_max": {
        "en": '"prices" accepts at most {max} models.',
        "es": "«prices» admite como máximo {max} modelos.",
        "ca": "«prices» admet com a màxim {max} models.",
    },
    "storage.settings.prices_bad_id": {
        "en": '"prices": "{model}" is not a valid model id.',
        "es": "«prices»: «{model}» no es un identificador de modelo válido.",
        "ca": "«prices»: «{model}» no és un identificador de model vàlid.",
    },
    "storage.settings.prices_no_model": {
        "en": '"prices": "{model}" names no model (nothing is left of it without the '
        "provider's prefix, the date or the context).",
        "es": "«prices»: «{model}» no identifica ningún modelo (sin el prefijo del proveedor, "
        "la fecha o el contexto no queda nada).",
        "ca": "«prices»: «{model}» no identifica cap model (sense el prefix del proveïdor, la "
        "data o el context no en queda res).",
    },
    "storage.settings.prices_same_model": {
        "en": '"prices": "{first}" and "{second}" are the same model ("{model}"). Keep only '
        "one of them.",
        "es": "«prices»: «{first}» y «{second}» son el mismo modelo («{model}»). Deja solo uno.",
        "ca": "«prices»: «{first}» i «{second}» són el mateix model («{model}»). Deixa'n només un.",
    },
    "storage.settings.price_object": {
        "en": '"{name}" must be an object with "input", "output", "cache_read" and "cache_write".',
        "es": "«{name}» debe ser un objeto con «input», «output», «cache_read» y «cache_write».",
        "ca": "«{name}» ha de ser un objecte amb «input», «output», «cache_read» i «cache_write».",
    },
    "storage.settings.price_range": {
        "en": '"{name}" must be a price between $0 and ${limit}.',
        "es": "«{name}» debe ser un precio entre 0 y {limit} $.",
        "ca": "«{name}» ha de ser un preu entre 0 i {limit} $.",
    },
    "storage.settings.bad_price": {
        "en": 'Invalid price for "{key}": it must be a number ≥ 0.',
        "es": "Precio no válido para «{key}»: debe ser un número ≥ 0.",
        "ca": "Preu invàlid per a «{key}»: ha de ser un nombre ≥ 0.",
    },
}
