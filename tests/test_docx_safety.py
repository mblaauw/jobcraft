import io
import zipfile
from xml.etree import ElementTree as ET

import pytest

from app import cv_uploads, docs, docx_safety
from tests.test_flexible_cv import cv_bytes

REL = "http://schemas.openxmlformats.org/package/2006/relationships"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def changed_package(
    *, target="https://example.test", kind="hyperlink", spoof=False, part="word/document.xml"
):
    raw = cv_bytes(4)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        content = {name: archive.read(name) for name in archive.namelist()}
    relation_file = part.rsplit("/", 1)[0] + "/_rels/" + part.rsplit("/", 1)[1] + ".rels"
    relationships = ET.fromstring(
        content.get(relation_file, f'<Relationships xmlns="{REL}"/>'.encode())
    )
    ET.SubElement(
        relationships,
        "{" + REL + "}Relationship",
        {
            "Id": "unsafe-test",
            "Target": target,
            "TargetMode": "External",
            "Type": R + "/" + kind,
        },
    )
    content[relation_file] = ET.tostring(relationships)
    root = ET.fromstring(content.get(part, f'<w:hdr xmlns:w="{W}"/>'.encode()))
    tag = "{" + W + "}drawing" if spoof else "{" + W + "}hyperlink"
    element = ET.SubElement(root, tag)
    element.set("{" + R + "}id", "unsafe-test")
    content[part] = ET.tostring(root)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, data in content.items():
            archive.writestr(name, data)
    return output.getvalue()


@pytest.mark.parametrize(
    "target", ["https://example.test/cv", "http://example.test", "mailto:person@example.test"]
)
def test_ordinary_hyperlinks_are_allowed(target):
    raw = changed_package(target=target)
    assert docs.inspect_cv(raw)["strong_count"] == 4


@pytest.mark.parametrize(
    ("kind", "target", "part", "spoof"),
    [
        ("image", "https://example.test/image.png", "word/document.xml", False),
        (
            "attachedTemplate",
            "file:///private/other-owner/template.dotx",
            "word/settings.xml",
            False,
        ),
        ("oleObject", "file:///private/other-owner/private.txt", "word/header1.xml", False),
        ("hyperlink", "file:///private/other-owner/private.txt", "word/document.xml", False),
        ("hyperlink", "//example.test/image.png", "word/document.xml", False),
        ("hyperlink", "https://example.test/image.png", "word/document.xml", True),
    ],
)
def test_external_resources_and_spoofed_hyperlinks_are_rejected(kind, target, part, spoof):
    raw = changed_package(kind=kind, target=target, part=part, spoof=spoof)
    with pytest.raises(ValueError):
        cv_uploads.stage(raw, "unsafe.docx", "session")
    assert not cv_uploads._directory().exists()


def test_duplicate_zip_entries_and_active_field_codes_are_rejected():
    raw = cv_bytes()
    duplicate = io.BytesIO(raw)
    with pytest.warns(UserWarning), zipfile.ZipFile(duplicate, "a") as archive:
        archive.writestr("word/document.xml", "duplicate")
    with pytest.raises(ValueError):
        docx_safety.validate_package(duplicate.getvalue())
    raw = changed_package()
    source = io.BytesIO(raw)
    output = io.BytesIO()
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(output, "w") as rewritten:
        for name in archive.namelist():
            data = archive.read(name)
            if name == "word/document.xml":
                root = ET.fromstring(data)
                field = ET.SubElement(root, "{" + W + "}instrText")
                field.text = 'INCLUDETEXT "file:///private/other-owner/secret"'
                data = ET.tostring(root)
            rewritten.writestr(name, data)
    with pytest.raises(ValueError):
        docx_safety.validate_package(output.getvalue())
