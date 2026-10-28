"""Local package checks; these do not replace a live Microsoft import/test."""

import base64
import json
import re
from pathlib import Path
from urllib.parse import unquote
from uuid import UUID
from zipfile import ZipFile

HERE = Path(__file__).resolve().parent


def parse(expression):
    """Parse the WDL subset used here without executing arbitrary source code."""
    tokens = re.findall(r"'(?:[^']|'')*'|[A-Za-z_$][A-Za-z0-9_$]*|-?\d+|[^\s]", expression[1:])
    position = 0

    def read():
        nonlocal position
        token = tokens[position]
        position += 1
        if token.startswith("'"):
            node = ("literal", token[1:-1].replace("''", "'"))
        elif token.lstrip("-").isdigit():
            node = ("literal", int(token))
        elif token in ("false", "true", "null"):
            node = ("literal", {"false": False, "true": True, "null": None}[token])
        else:
            assert tokens[position] == "(", (expression, position)
            position += 1
            args = []
            while tokens[position] != ")":
                args.append(read())
                if tokens[position] != ",":
                    break
                position += 1
            assert tokens[position] == ")", (expression, position)
            position += 1
            node = ("call", token, args)
        while position < len(tokens) and tokens[position] in ("?", "["):
            if tokens[position] == "?":
                position += 1
            assert tokens[position] == "["
            position += 1
            key = read()
            assert tokens[position] == "]"
            position += 1
            node = ("lookup", node, key)
        return node

    node = read()
    assert position == len(tokens), (expression, tokens[position:])
    return node


def evaluate(node, attachment=None, bodies=None):
    if node[0] == "literal":
        return node[1]
    if node[0] == "lookup":
        obj = evaluate(node[1], attachment, bodies)
        key = evaluate(node[2], attachment, bodies)
        return obj.get(key) if obj is not None else None
    functions = {
        "item": lambda: attachment,
        "items": lambda _: attachment,
        "body": lambda name: bodies[name],
        "triggerBody": lambda: (bodies or {}).get("__trigger__", {}),
        "and": lambda *args: all(args),
        "or": lambda *args: any(args),
        "contains": lambda text, part: part in text,
        "startsWith": lambda text, prefix: text.startswith(prefix),
        "endsWith": lambda text, suffix: text.endswith(suffix),
        "toLower": str.lower,
        "coalesce": lambda *args: next((x for x in args if x is not None), None),
        "equals": lambda a, b: a == b,
        "trim": str.strip,
        "substring": lambda text, start, count: text[start:start + count],
        "slice": lambda text, start, end: text[start:end],
        "isInt": lambda text: bool(re.fullmatch(r"[+-]?[0-9]+", text.strip())) and -(2**63) <= int(text) < 2**63,
        "sub": lambda a, b: a - b,
        "length": len,
        "replace": str.replace,
        "decodeUriComponent": unquote,
        "take": lambda text, count: text[:count],
        "concat": lambda *args: "".join(args),
        "guid": lambda: "12345678-1234-4234-9234-123456789abc",
        "base64ToBinary": lambda value: base64.b64decode(value, validate=True),
    }
    return functions[node[1]](*(evaluate(x, attachment, bodies) for x in node[2]))


def main():
    with ZipFile(HERE / "build-input" / "legacy-flow-package.zip") as archive:
        assert archive.testzip() is None
        documents = {name: json.loads(archive.read(name)) for name in archive.namelist()}
    root = documents["manifest.json"]
    flow_id, = documents["Microsoft.Flow/flows/manifest.json"]["flowAssets"]["assetPaths"]
    base = "Microsoft.Flow/flows/" + flow_id + "/"
    assert set(documents) == {
        "manifest.json", "Microsoft.Flow/flows/manifest.json",
        base + "definition.json", base + "apisMap.json", base + "connectionsMap.json",
    }
    resources = root["resources"]
    for identifier, resource in resources.items():
        UUID(identifier)
        assert set(resource["dependsOn"]).issubset(resources)
    flow = documents[base + "definition.json"]
    assert flow["name"] == flow_id
    assert resources[flow_id]["suggestedCreationType"] == "New"
    refs = flow["properties"]["connectionReferences"]
    assert set(refs) == {"shared_office365", "shared_onedriveforbusiness"}
    for key, reference in refs.items():
        assert resources[documents[base + "apisMap.json"][key]]["id"] == reference["id"]
        mapped_connection = resources[documents[base + "connectionsMap.json"][key]]
        assert mapped_connection["dependsOn"] == [documents[base + "apisMap.json"][key]]

    workflow = flow["properties"]["definition"]
    names = set()
    operations = []

    def walk_actions(actions):
        names.update(actions)
        for action in actions.values():
            assert set(action.get("runAfter", {})).issubset(actions)
            if "host" in action.get("inputs", {}):
                host = action["inputs"]["host"]
                assert host["connectionName"] in refs
                assert host["apiId"] == refs[host["connectionName"]]["id"]
                operations.append(host["operationId"])
            walk_actions(action.get("actions", {}))

    walk_actions(workflow["actions"])
    walk_actions(workflow["triggers"])
    assert sorted(operations) == ["CreateFile", "GetAttachment_V2", "OnNewEmailV3"]

    def walk_values(value):
        if isinstance(value, str) and value.startswith("@"):
            parse(value)
            for reference in re.findall(r"(?:outputs|body|items)\('([^']+)'\)", value):
                assert reference in names
        elif isinstance(value, dict):
            for child in value.values():
                walk_values(child)
        elif isinstance(value, list):
            for child in value:
                walk_values(child)

    walk_values(workflow)
    trigger, = workflow["triggers"].values()
    assert trigger["inputs"]["parameters"]["includeAttachments"] is False
    assert trigger["splitOn"] == "@triggerOutputs()?['body/value']"
    assert workflow["actions"]["OneDrive_folder"]["inputs"] == "/Fakturaer"
    predicate = parse(workflow["actions"]["Filter_invoice_PDFs"]["inputs"]["where"])
    cases = [
        ({"name": "Faktura_TEST.pdf"}, False),
        ({"name": "faktura_123.PDF"}, False),
        ({"name": "FAKTURA_X.PdF", "isInline": False}, False),
        ({"name": "Kreditnota_TEST.pdf"}, True),
        ({"name": "KREDITNOTA_TEST.PDF", "isInline": False}, True),
        ({"name": "Faktura fra Royal Unibrew TEST.pdf"}, True),
        ({"name": "Royal Unibrew A_S_TEST.pdf"}, True),
        ({"name": "royal unibrew TEST.PdF"}, True),
        ({"name": "inco Aarhus TEST.pdf"}, True),
        ({"name": "INCO Kobenhavn TEST.PDF", "isInline": False}, True),
        ({"name": "Faktura_123.pdf", "isInline": True}, False),
        ({"name": "Kreditnota_TEST.pdf", "isInline": True}, False),
        ({"name": "Royal Unibrew TEST.pdf", "isInline": True}, False),
        ({"name": "inco TEST.pdf", "isInline": True}, False),
        ({"name": "Other.pdf"}, False),
        ({"name": "Invoice_TEST.pdf"}, False),
        ({"name": "KreditnotaTEST.pdf"}, False),
        ({"name": "TEST inco.pdf"}, False),
        ({"name": "Faktura_123.png"}, False),
        ({"name": "Kreditnota_TEST.docx"}, False),
        ({"name": "Royal Unibrew TEST.png"}, False),
        ({"name": "inco TEST.xlsx"}, False),
        ({"name": "Faktura_123.pdf.exe"}, False),
        ({"name": "inco TEST.pdf.exe"}, False),
        ({"name": None}, False),
        ({}, False),
    ]
    for attachment, expected in cases:
        assert evaluate(predicate, attachment) == expected, attachment
    mail_cases = [
        ("Faktura_1234567.pdf", "Faktura 1234567 fra inco Aarhus A/S", True),
        ("Faktura_7654321.pdf", "Faktura 7654321 fra inco København Cash & Carry A/S", True),
        ("FAKTURA_1234567.PDF", "FW: FAKTURA 1234567 FRA INCO AARHUS A/S", True),
        ("Faktura_TEST.pdf", "Faktura TEST fra inco Aarhus A/S", False),
        ("Faktura_X.pdf", "Faktura X fra inco Aarhus A/S", False),
        ("Faktura_.pdf", "Faktura fra inco Aarhus A/S", False),
        ("Faktura_1234567.pdf", "Faktura fra en anden leverandør", False),
        ("Faktura_1234567.pdf", None, False),
        ("Faktura_1234567.png", "Faktura 1234567 fra inco Aarhus A/S", False),
        ("Faktura_1234567.pdf.exe", "Faktura 1234567 fra inco Aarhus A/S", False),
        ("Faktura_1234567_extra.pdf", "Faktura 1234567 fra inco Aarhus A/S", False),
        ("x.pdf", "Faktura 1234567 fra inco Aarhus A/S", False),
        (None, "Faktura 1234567 fra inco Aarhus A/S", False),
        ("Kreditnota_K-1234567.pdf", "Kreditnota K-1234567 fra inco København Cash & Carry A/S", True),
        ("Kreditnota_K-7654321.pdf", "Kreditnota K-7654321 fra inco Aarhus A/S", True),
        ("Faktura fra Royal Unibrew 1234567890.pdf", "Faktura fra Royal Unibrew 1234567890", True),
        ("Royal Unibrew kredit TEST.pdf", "Faktura fra Royal Unibrew 9876543210", True),
    ]
    for name, subject, expected in mail_cases:
        for sender in ("supplier@example.com", "forwarder@example.com"):
            message = {"subject": subject, "from": sender}
            attachment = {"name": name, "isInline": False}
            assert evaluate(predicate, attachment, {"__trigger__": message}) == expected, (name, subject)
            attachment["isInline"] = True
            assert evaluate(predicate, attachment, {"__trigger__": message}) is False, (name, subject, "inline")
    loop = workflow["actions"]["Save_each_invoice"]["actions"]
    filename = parse(loop["Unique_file_name"]["inputs"])
    normal = evaluate(filename, {"name": "Faktura_123.pdf"})
    assert normal == "Faktura_123_12345678-1234-4234-9234-123456789abc.pdf"
    odd = evaluate(filename, {"name": "Faktura_'#%/:\\*?\"<>|.PDF"})
    assert not any(character in odd for character in "'#%/:\\*?\"<>|")
    long = evaluate(filename, {"name": "Faktura_" + "X" * 300 + ".pdf"})
    assert len(long) == 120 + 1 + 36 + 4
    binary = parse(loop["Create_file_in_OneDrive"]["inputs"]["parameters"]["body"])
    payload = b"%PDF-1.4\n\x00\xff\x80\n%%EOF"
    result = evaluate(binary, bodies={"Get_attachment_V2": {"contentBytes": base64.b64encode(payload).decode()}})
    assert result == payload
    print("PASS: ZIP integrity, five package files, resources, connector maps, action dependencies, expression syntax.")
    print(f"PASS: {len(cases)} attachment filter cases; normal, unsafe and long filenames; binary payload preserved.")
    print(f"PASS: {len(mail_cases)} mail/attachment scenarios, each with two senders and inline exclusion; synthetic examples only.")
    print("NOT VERIFIED: Microsoft import validation or authenticated Outlook/OneDrive execution.")


if __name__ == "__main__":
    main()
