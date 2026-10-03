"""Texts of attachments.py and pdf_facts.py: upload checks, PDF analysis, the page notes
(keys ``attachments.*``)."""

from __future__ import annotations

from typing import Final

from agentic_os.i18n import Text

MESSAGES: Final[dict[str, Text]] = {
    # -- the type ------------------------------------------------------------------------
    "attachments.unsupported": {
        "en": (
            "This type of file is not accepted. You can attach images (PNG, JPEG, GIF or "
            "WebP), PDFs and text files (UTF-8)."
        ),
        "es": (
            "Este tipo de archivo no se admite. Puedes adjuntar imágenes (PNG, JPEG, GIF o "
            "WebP), PDF y archivos de texto (UTF-8)."
        ),
        "ca": (
            "Aquest tipus de fitxer no s'admet. Pots adjuntar imatges (PNG, JPEG, GIF o WebP), "
            "PDF i fitxers de text (UTF-8)."
        ),
    },
    "attachments.svg": {
        "en": (
            "SVG images are not accepted, because they can carry code. Convert it to PNG and "
            "attach it again."
        ),
        "es": (
            "Las imágenes SVG no se admiten, porque pueden llevar código. Conviértela a PNG y "
            "vuelve a adjuntarla."
        ),
        "ca": (
            "Les imatges SVG no s'admeten, perquè poden portar codi. Converteix-la a PNG i "
            "torna-la a adjuntar."
        ),
    },
    "attachments.heic": {
        "en": (
            "HEIC images are not accepted. Convert it to JPEG (or take a screenshot of it) "
            "and attach it again."
        ),
        "es": (
            "Las imágenes HEIC no se admiten. Conviértela a JPEG (o hazle una captura) y "
            "vuelve a adjuntarla."
        ),
        "ca": (
            "Les imatges HEIC no s'admeten. Converteix-la a JPEG (o fes-ne una captura) i "
            "torna-la a adjuntar."
        ),
    },
    # -- the file and its name -----------------------------------------------------------
    "attachments.empty": {
        "en": "The file is empty.",
        "es": "El archivo está vacío.",
        "ca": "El fitxer és buit.",
    },
    "attachments.name_required": {
        "en": 'The file name is required (the "name" parameter).',
        "es": "Hay que indicar el nombre del archivo (parámetro «name»).",
        "ca": "Cal indicar el nom del fitxer (paràmetre «name»).",
    },
    "attachments.bad_name": {
        "en": "The file name is not valid.",
        "es": "El nombre del archivo no es válido.",
        "ca": "El nom del fitxer no és vàlid.",
    },
    # The size limit of each kind: whole sentences, since the kind's article differs.
    "attachments.too_large.image": {
        "en": "The file is too large: an image can be at most {size}.",
        "es": "El archivo es demasiado grande: una imagen puede tener como máximo {size}.",
        "ca": "El fitxer és massa gran: una imatge pot tenir com a molt {size}.",
    },
    "attachments.too_large.pdf": {
        "en": "The file is too large: a PDF can be at most {size}.",
        "es": "El archivo es demasiado grande: un PDF puede tener como máximo {size}.",
        "ca": "El fitxer és massa gran: un PDF pot tenir com a molt {size}.",
    },
    "attachments.too_large.text": {
        "en": "The file is too large: a text file can be at most {size}.",
        "es": "El archivo es demasiado grande: un archivo de texto puede tener como máximo {size}.",
        "ca": "El fitxer és massa gran: un fitxer de text pot tenir com a molt {size}.",
    },
    # -- images and thumbnails -----------------------------------------------------------
    "attachments.bad_image": {
        "en": "The image's dimensions could not be read: the file is not valid.",
        "es": "No se han podido leer las dimensiones de la imagen: el archivo no es válido.",
        "ca": "No s'han pogut llegir les dimensions de la imatge: el fitxer no és vàlid.",
    },
    "attachments.image_too_big": {
        "en": "The image is {width} x {height} pixels: at most {max} per side.",
        "es": "La imagen mide {width} x {height} píxeles: como máximo {max} por lado.",
        "ca": "La imatge fa {width} x {height} píxels: com a molt {max} per costat.",
    },
    "attachments.thumbnail_type": {
        "en": "The thumbnail must be a PNG or WebP image.",
        "es": "La miniatura debe ser una imagen PNG o WebP.",
        "ca": "La miniatura ha de ser una imatge PNG o WebP.",
    },
    "attachments.thumbnail_bad": {
        "en": "The thumbnail's dimensions could not be read.",
        "es": "No se han podido leer las dimensiones de la miniatura.",
        "ca": "No s'han pogut llegir les dimensions de la miniatura.",
    },
    "attachments.thumbnail_too_large": {
        "en": "The thumbnail is too large: at most {size}.",
        "es": "La miniatura es demasiado grande: como máximo {size}.",
        "ca": "La miniatura és massa gran: com a molt {size}.",
    },
    "attachments.thumbnail_too_big": {
        "en": "The thumbnail is {width} x {height} pixels: at most {max} per side.",
        "es": "La miniatura mide {width} x {height} píxeles: como máximo {max} por lado.",
        "ca": "La miniatura fa {width} x {height} píxels: com a molt {max} per costat.",
    },
    # -- PDFs ----------------------------------------------------------------------------
    "attachments.pdf_invalid": {
        "en": "The PDF is not valid or is damaged.",
        "es": "El PDF no es válido o está dañado.",
        "ca": "El PDF no és vàlid o està malmès.",
    },
    "attachments.pdf_encrypted": {
        "en": (
            "The PDF is encrypted or protected with a password. Remove the protection and "
            "attach it again."
        ),
        "es": (
            "El PDF está cifrado o protegido con contraseña. Quítale la protección y vuelve "
            "a adjuntarlo."
        ),
        "ca": (
            "El PDF està xifrat o protegit amb contrasenya. Treu-ne la protecció i torna'l a "
            "adjuntar."
        ),
    },
    "attachments.pdf_empty": {
        "en": "The PDF has no pages.",
        "es": "El PDF no tiene ninguna página.",
        "ca": "El PDF no té cap pàgina.",
    },
    "attachments.pdf_pages": {
        "en": "The PDF has {pages} pages: at most {max}.",
        "es": "El PDF tiene {pages} páginas: como máximo {max}.",
        "ca": "El PDF té {pages} pàgines: com a molt {max}.",
    },
    "attachments.pdf_timeout": {
        "en": (
            "The PDF could not be read in {seconds} seconds. Try a simpler or smaller "
            "version of it."
        ),
        "es": (
            "No se ha podido leer el PDF en {seconds} segundos. Prueba con una versión más "
            "sencilla o más pequeña."
        ),
        "ca": (
            "No s'ha pogut llegir el PDF en {seconds} segons. Prova'n una versió més senzilla "
            "o més petita."
        ),
    },
}
