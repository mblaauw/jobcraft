"""Reject active or externally loaded DOCX resources before template conversion.

This is a conservative format policy, not a LibreOffice sandbox. Ordinary embedded
raster images and explicit web/mail hyperlinks are supported; active objects,
external resources, legacy VML and executable field instructions are not.
"""

import io
import posixpath
import re
import zipfile
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

REL_NAMESPACES = {
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "http://purl.oclc.org/ooxml/officeDocument/relationships",
}
WORD_NAMESPACES = {
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "http://purl.oclc.org/ooxml/wordprocessingml/main",
}
MESSAGE = "Gebruik een DOCX zonder externe afbeeldingen, gekoppelde sjablonen, actieve objecten of uitvoerbare veldcodes."


def validate_package(raw: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = archive.namelist()
            if (
                len(names) != len(set(names))
                or len(names) > 1000
                or sum(item.file_size for item in archive.infolist()) > 20 * 1024 * 1024
            ):
                raise ValueError(MESSAGE)
            roots = {}
            for name in names:
                lowered = name.lower()
                if (
                    "vbaproject" in lowered
                    or "/embeddings/" in lowered
                    or "/activex/" in lowered
                    or lowered.endswith((".bin", ".vml"))
                ):
                    raise ValueError(MESSAGE)
                if "/media/" in lowered and not lowered.endswith(
                    (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff")
                ):
                    raise ValueError(MESSAGE)
                if lowered.endswith((".xml", ".rels")):
                    xml = archive.read(name)
                    if b"<!DOCTYPE" in xml.upper():
                        raise ValueError(MESSAGE)
                    root = roots[name] = ET.fromstring(xml)
                    for element in root.iter():
                        local = element.tag.rsplit("}", 1)[-1]
                        if element.tag.startswith("{urn:schemas-microsoft-com:vml}") or local in (
                            "altChunk",
                            "OLEObject",
                            "object",
                            "control",
                        ):
                            raise ValueError(MESSAGE)
                        instruction = None
                        if local == "instrText":
                            instruction = element.text or ""
                        elif local == "fldSimple":
                            instruction = next(
                                (
                                    value
                                    for key, value in element.attrib.items()
                                    if key.rsplit("}", 1)[-1] == "instr"
                                ),
                                "",
                            )
                        if instruction is not None and not re.fullmatch(
                            r"\s*(?:PAGE|NUMPAGES|SECTIONPAGES)\s*(?:\\\*\s*MERGEFORMAT)?\s*",
                            instruction,
                            re.I,
                        ):
                            raise ValueError(MESSAGE)
            for name, root in roots.items():
                if not name.endswith(".rels"):
                    continue
                source = name.replace("/_rels/", "/")[:-5] if name != "_rels/.rels" else None
                ids = [relation.get("Id") for relation in root]
                if None in ids or len(ids) != len(set(ids)):
                    raise ValueError(MESSAGE)
                for relation in root:
                    target = relation.get("Target", "")
                    mode = relation.get("TargetMode", "Internal")
                    parsed = urlsplit(target)
                    if mode not in ("Internal", "External") or not target or "\\" in target:
                        raise ValueError(MESSAGE)
                    if mode == "Internal":
                        resolved = posixpath.normpath(
                            posixpath.join(posixpath.dirname(source or ""), parsed.path)
                        )
                        resolved = resolved.lstrip("/")
                        if (
                            parsed.scheme
                            or parsed.netloc
                            or parsed.query
                            or resolved.startswith("../")
                            or resolved not in names
                        ):
                            raise ValueError(MESSAGE)
                        continue
                    if (
                        relation.get("Type") not in {uri + "/hyperlink" for uri in REL_NAMESPACES}
                        or parsed.scheme.lower() not in ("http", "https", "mailto")
                        or not (
                            parsed.path if parsed.scheme.lower() == "mailto" else parsed.hostname
                        )
                    ):
                        raise ValueError(MESSAGE)
                    if source not in roots:
                        raise ValueError(MESSAGE)
                    usages = [
                        (element, attribute)
                        for element in roots[source].iter()
                        for attribute, value in element.attrib.items()
                        if value == relation.get("Id")
                        and attribute.startswith(tuple("{" + uri + "}" for uri in REL_NAMESPACES))
                    ]
                    if not usages or any(
                        element.tag not in {"{" + uri + "}hyperlink" for uri in WORD_NAMESPACES}
                        or attribute.rsplit("}", 1)[-1] != "id"
                        for element, attribute in usages
                    ):
                        raise ValueError(MESSAGE)
    except (zipfile.BadZipFile, ET.ParseError, KeyError, TypeError, OSError):
        raise ValueError("Geen geldig, veilig leesbaar DOCX-bestand.") from None
