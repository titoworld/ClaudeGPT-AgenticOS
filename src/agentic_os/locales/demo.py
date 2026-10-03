"""Texts of providers/fake.py: the demo answers (AOS_*_MODE=fake) (keys ``demo.*``).

The fake provider writes them in the turn's language. What the engine parses in them
stays in the code, never here: the sections' tags, ``UNCHANGED``, the ``- [kind] `` of a
change; these are only the prose."""

from __future__ import annotations

from typing import Final

from agentic_os.i18n import Text

MESSAGES: Final[dict[str, Text]] = {
    # -- answers --------------------------------------------------------------------------
    "demo.topic_default": {
        "en": "your question",
        "es": "tu pregunta",
        "ca": "la teva pregunta",
    },
    "demo.quoted": {
        "en": "“{text}”",
        "es": "«{text}»",
        "ca": "«{text}»",
    },
    "demo.claude.intro_1": {
        "en": "Here is how I would approach it",
        "es": "Así es como lo enfocaría",
        "ca": "Vet aquí com ho enfocaria",
    },
    "demo.claude.intro_2": {
        "en": "I would sum it up in three steps",
        "es": "Lo resumiría en tres pasos",
        "ca": "Ho resumiria en tres passos",
    },
    "demo.claude.intro_3": {
        "en": "The key is to go one step at a time",
        "es": "La clave es ir por partes",
        "ca": "La clau és anar per parts",
    },
    "demo.claude.answer": {
        "en": (
            "### {topic}\n\n"
            "{intro}:\n\n"
            "1. **Define the goal.** Pin down what you want to achieve and what constraints "
            "you have (time, budget, tools).\n"
            "2. **Start with the simplest path.** Try the smallest solution that works and "
            "measure its result before adding complexity.\n"
            "3. **Check the edge cases.** Think about what can go wrong and how you will "
            "detect it.\n\n"
            "> Demo answer: set `AOS_{mode}_MODE=cli` or `api` to talk to the real model."
        ),
        "es": (
            "### {topic}\n\n"
            "{intro}:\n\n"
            "1. **Define el objetivo.** Concreta qué quieres conseguir y qué restricciones "
            "tienes (tiempo, presupuesto, herramientas).\n"
            "2. **Empieza por el camino más sencillo.** Prueba la solución mínima que "
            "funcione y mide su resultado antes de añadir complejidad.\n"
            "3. **Revisa los casos límite.** Piensa qué puede fallar y cómo lo detectarás.\n\n"
            "> Respuesta de demostración: configura `AOS_{mode}_MODE=cli` o `api` para "
            "hablar con el modelo real."
        ),
        "ca": (
            "### {topic}\n\n"
            "{intro}:\n\n"
            "1. **Defineix l'objectiu.** Concreta què vols aconseguir i quines "
            "restriccions tens (temps, pressupost, eines).\n"
            "2. **Comença pel camí més senzill.** Prova la solució mínima que funcioni "
            "i mesura'n el resultat abans d'afegir complexitat.\n"
            "3. **Revisa els casos límit.** Pensa què pot fallar i com ho detectaràs.\n\n"
            "> Resposta de demostració: configura `AOS_{mode}_MODE=cli` o `api` "
            "per parlar amb el model real."
        ),
    },
    "demo.chatgpt.tip_1": {
        "en": "Write each decision down in one line",
        "es": "Documenta cada decisión en una línea",
        "ca": "Documenta cada decisió en una línia",
    },
    "demo.chatgpt.tip_2": {
        "en": "Give each test a deadline",
        "es": "Pon una fecha límite a cada prueba",
        "ca": "Posa una data límit a cada prova",
    },
    "demo.chatgpt.tip_3": {
        "en": "Always compare with an alternative",
        "es": "Compara siempre con una alternativa",
        "ca": "Compara sempre amb una alternativa",
    },
    "demo.chatgpt.answer": {
        "en": (
            "**Quick answer:** on “{topic}”, what matters most is being clear about the "
            "expected result and moving forward in short iterations.\n\n"
            "| Aspect | Proposal |\n"
            "| --- | --- |\n"
            "| First step | Write the goal in one sentence |\n"
            "| Tool | The simplest one that solves the problem |\n"
            "| Validation | A small test before scaling up |\n\n"
            "- **Tip:** {tip}.\n"
            "- **Common mistake:** optimizing before knowing where the bottleneck is.\n\n"
            "*Demo answer (`AOS_{mode}_MODE=fake`).*"
        ),
        "es": (
            "**Respuesta rápida:** sobre «{topic}», lo más importante es tener claro el "
            "resultado esperado y avanzar con iteraciones cortas.\n\n"
            "| Aspecto | Propuesta |\n"
            "| --- | --- |\n"
            "| Primer paso | Escribir el objetivo en una frase |\n"
            "| Herramienta | La más simple que resuelva el problema |\n"
            "| Validación | Una prueba pequeña antes de escalar |\n\n"
            "- **Consejo:** {tip}.\n"
            "- **Error habitual:** optimizar antes de saber dónde está el cuello de botella.\n\n"
            "*Respuesta de demostración (`AOS_{mode}_MODE=fake`).*"
        ),
        "ca": (
            "**Resposta ràpida:** sobre «{topic}», el més important és tenir clar el "
            "resultat esperat i avançar amb iteracions curtes.\n\n"
            "| Aspecte | Proposta |\n"
            "| --- | --- |\n"
            "| Primer pas | Escriure l'objectiu en una frase |\n"
            "| Eina | La més simple que resolgui el problema |\n"
            "| Validació | Una prova petita abans d'escalar |\n\n"
            "- **Consell:** {tip}.\n"
            "- **Error habitual:** optimitzar abans de saber on és el coll d'ampolla.\n\n"
            "*Resposta de demostració (`AOS_{mode}_MODE=fake`).*"
        ),
    },
    "demo.attachments_received": {
        "en": "**Attachments received:** {labels}.",
        "es": "**Adjuntos recibidos:** {labels}.",
        "ca": "**Adjunts rebuts:** {labels}.",
    },
    "demo.kind.image": {
        "en": "image",
        "es": "imagen",
        "ca": "imatge",
    },
    "demo.kind.pdf": {
        "en": "PDF",
        "es": "PDF",
        "ca": "PDF",
    },
    "demo.kind.text": {
        "en": "text file",
        "es": "archivo de texto",
        "ca": "fitxer de text",
    },
    "demo.pages.one": {
        "en": "1 page",
        "es": "1 página",
        "ca": "1 pàgina",
    },
    "demo.pages.other": {
        "en": "{count} pages",
        "es": "{count} páginas",
        "ca": "{count} pàgines",
    },
    "demo.text_only": {
        "en": "only the extracted text",
        "es": "solo el texto extraído",
        "ca": "només el text extret",
    },
    # -- a debate's revisions and synthesis -----------------------------------------------
    "demo.revision.agreed": {
        "en": "- No relevant errors: {other}'s answer is correct and complete.",
        "es": "- Ningún error relevante: la respuesta de {other} es correcta y completa.",
        "ca": "- Cap error rellevant: la resposta de {other} és correcta i completa.",
    },
    "demo.revision.critique": {
        "en": (
            "- {other}'s answer lacks a concrete example.\n"
            "- It does not say how to check that the solution works."
        ),
        "es": (
            "- A la respuesta de {other} le falta un ejemplo concreto.\n"
            "- No dice cómo se comprueba que la solución funciona."
        ),
        "ca": (
            "- A la resposta de {other} li falta un exemple concret.\n"
            "- No diu com es comprova que la solució funciona."
        ),
    },
    "demo.revision.addition": {
        "en": (
            "**After reviewing {other}'s answer:** I am adding an example. If the goal is "
            "to cut a 10-minute process down to 2, measure it first, then apply a single "
            "change and measure again."
        ),
        "es": (
            "**Después de revisar la respuesta de {other}:** añado un ejemplo. Si el "
            "objetivo es reducir un proceso de 10 minutos a 2, primero mídelo, después "
            "aplica un solo cambio y vuelve a medir."
        ),
        "ca": (
            "**Després de revisar la resposta de {other}:** hi afegeixo un exemple. "
            "Si l'objectiu és reduir un procés de 10 minuts a 2, primer mesura'l, "
            "després aplica un sol canvi i torna a mesurar."
        ),
    },
    "demo.synthesis": {
        "en": (
            "## {topic}\n\n"
            "Claude and ChatGPT agree on the essentials:\n\n"
            "1. **A clear goal:** write down in one sentence what you want to achieve.\n"
            "2. **Short iterations:** start with the simplest solution and measure it.\n"
            "3. **Validation:** test the edge cases before calling it done.\n\n"
            "| Step | How to know it is done |\n"
            "| --- | --- |\n"
            "| Goal | It is written down and measurable |\n"
            "| Prototype | It works with a real case |\n"
            "| Review | The common mistakes are ruled out |\n\n"
            "*Demo synthesis written without any real model.*"
        ),
        "es": (
            "## {topic}\n\n"
            "Claude y ChatGPT coinciden en lo esencial:\n\n"
            "1. **Objetivo claro:** escribe en una frase qué quieres conseguir.\n"
            "2. **Iteraciones cortas:** empieza por la solución más sencilla y mídela.\n"
            "3. **Validación:** prueba los casos límite antes de darlo por bueno.\n\n"
            "| Paso | Cómo saber que está hecho |\n"
            "| --- | --- |\n"
            "| Objetivo | Está escrito y es medible |\n"
            "| Prototipo | Funciona con un caso real |\n"
            "| Revisión | Los errores habituales están descartados |\n\n"
            "*Síntesis de demostración generada sin ningún modelo real.*"
        ),
        "ca": (
            "## {topic}\n\n"
            "Claude i ChatGPT coincideixen en l'essencial:\n\n"
            "1. **Objectiu clar:** escriu en una frase què vols aconseguir.\n"
            "2. **Iteracions curtes:** comença per la solució més senzilla i mesura-la.\n"
            "3. **Validació:** prova els casos límit abans de donar-ho per bo.\n\n"
            "| Pas | Com saber que està fet |\n"
            "| --- | --- |\n"
            "| Objectiu | Està escrit i és mesurable |\n"
            "| Prototip | Funciona amb un cas real |\n"
            "| Revisió | Els errors habituals estan descartats |\n\n"
            "*Síntesi de demostració generada sense cap model real.*"
        ),
    },
    # -- a refine turn: the reviews' proposals, the merge, the edits and shortenings ----
    "demo.refine.claude.defect": {
        "en": (
            "Step 2: it does not say how the result is measured — without a measure it "
            "cannot be validated."
        ),
        "es": "Paso 2: no dice cómo se mide el resultado — sin una medida no se puede validar.",
        "ca": "Pas 2: no diu com es mesura el resultat — sense una mesura no es pot validar.",
    },
    "demo.refine.claude.clarity": {
        "en": "Step 1: “in one sentence” is vague — add a short example.",
        "es": "Paso 1: «en una frase» es vago — añade un ejemplo corto.",
        "ca": "Pas 1: «en una frase» és vague — posa-hi un exemple curt.",
    },
    "demo.refine.chatgpt.defect": {
        "en": (
            "Table: the “Prototype” row does not say how many cases are enough — the brief "
            "asks for it to be checkable."
        ),
        "es": (
            "Tabla: la fila «Prototipo» no dice con cuántos casos basta — el encargo pide "
            "poder comprobarlo."
        ),
        "ca": (
            "Taula: la fila «Prototip» no diu amb quants casos n'hi ha prou — "
            "l'encàrrec demana poder-ho comprovar."
        ),
    },
    "demo.refine.chatgpt.clarity": {
        "en": "Ending: the demo note is distracting — leave it alone on the last line.",
        "es": "Final: la nota de demostración distrae — déjala sola en la última línea.",
        "ca": "Final: la nota de demostració distreu — deixa-la sola a l'última línia.",
    },
    "demo.refine.document": {
        "en": (
            "## {topic}\n\n"
            "1. **A clear goal:** write down in one sentence what you want to achieve.\n"
            "2. **Short iterations:** start with the simplest solution and measure it.\n"
            "3. **Validation:** test the edge cases before calling it done.\n\n"
            "| Step | How to know it is done |\n"
            "| --- | --- |\n"
            "| Goal | It is written down and measurable |\n"
            "| Prototype | It works with a real case |\n"
            "| Review | The common mistakes are ruled out |\n\n"
            "*Demo document written without any real model.*"
        ),
        "es": (
            "## {topic}\n\n"
            "1. **Objetivo claro:** escribe en una frase qué quieres conseguir.\n"
            "2. **Iteraciones cortas:** empieza por la solución más sencilla y mídela.\n"
            "3. **Validación:** prueba los casos límite antes de darlo por bueno.\n\n"
            "| Paso | Cómo saber que está hecho |\n"
            "| --- | --- |\n"
            "| Objetivo | Está escrito y es medible |\n"
            "| Prototipo | Funciona con un caso real |\n"
            "| Revisión | Los errores habituales están descartados |\n\n"
            "*Documento de demostración escrito sin ningún modelo real.*"
        ),
        "ca": (
            "## {topic}\n\n"
            "1. **Objectiu clar:** escriu en una frase què vols aconseguir.\n"
            "2. **Iteracions curtes:** comença per la solució més senzilla i mesura-la.\n"
            "3. **Validació:** prova els casos límit abans de donar-ho per bo.\n\n"
            "| Pas | Com saber que està fet |\n"
            "| --- | --- |\n"
            "| Objectiu | Està escrit i és mesurable |\n"
            "| Prototip | Funciona amb un cas real |\n"
            "| Revisió | Els errors habituals estan descartats |\n\n"
            "*Document de demostració escrit sense cap model real.*"
        ),
    },
    "demo.refine.merge_steps": {
        "en": "The numbered steps come from Claude's answer.",
        "es": "Los pasos numerados vienen de la respuesta de Claude.",
        "ca": "Els passos numerats vénen de la resposta de Claude.",
    },
    "demo.refine.merge_table": {
        "en": "The checklist table comes from ChatGPT's answer.",
        "es": "La tabla de comprobación viene de la respuesta de ChatGPT.",
        "ca": "La taula de comprovació ve de la resposta de ChatGPT.",
    },
    # The edits' new lines: «**Change 2:** …».
    "demo.refine.change": {
        "en": "Change",
        "es": "Cambio",
        "ca": "Canvi",
    },
    "demo.refine.general_review": {
        "en": "General review.",
        "es": "Revisión general.",
        "ca": "Revisió general.",
    },
    "demo.refine.shortened": {
        "en": "Shortens the document to the word limit.",
        "es": "Acorta el documento hasta el límite de palabras.",
        "ca": "Escurça el document fins al límit de paraules.",
    },
    # -- the rest -------------------------------------------------------------------------
    # {previous}: a space and the summary so far, or nothing.
    "demo.summary": {
        "en": "Summary (demo):{previous} The user asked about {listed}.",
        "es": "Resumen (demo):{previous} El usuario ha preguntado por {listed}.",
        "ca": "Resum (demostració):{previous} L'usuari ha preguntat per {listed}.",
    },
    "demo.summary_topics": {
        "en": "various topics",
        "es": "temas diversos",
        "ca": "temes diversos",
    },
    "demo.refusal": {
        "en": "I can't help with this request.",
        "es": "No puedo ayudar con esta petición.",
        "ca": "No puc ajudar amb aquesta petició.",
    },
}
