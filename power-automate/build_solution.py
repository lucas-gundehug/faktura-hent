"""Package the invoice flow as an unmanaged Dataverse solution using Microsoft's CLI."""

import copy
import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

from build_package import HERE, TITLE, make_package, resource_id

PAC = HERE / ".tools/pac-cli/tools/pac.exe"
PROJECT = HERE / "InvoiceAutomation"
SOURCE = PROJECT / "src"
TARGET = HERE / "build-input" / "solution-container-v4.zip"
VERSION = "1.0.0.3"
REFERENCES = {
    "shared_office365": ("lgi_invoice_outlook", "Invoice automation - Outlook"),
    "shared_onedriveforbusiness": ("lgi_invoice_onedrive", "Invoice automation - OneDrive"),
}


def xml_save(tree, path):
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def main():
    if not PAC.exists():
        raise SystemExit("Microsoft Power Platform CLI must be present in .tools/pac-cli/tools/pac.exe")
    if not (SOURCE / "Other/Solution.xml").exists():
        subprocess.run([str(PAC), "solution", "init", "--publisher-name", "LucasInvoiceAutomation",
                        "--publisher-prefix", "lgi", "--outputDirectory", str(PROJECT)], check=True)
    package = make_package()
    flow_id = resource_id("flow")
    legacy_flow = package[f"Microsoft.Flow/flows/{flow_id}/definition.json"]
    # Dataverse clientdata is not the legacy export's ARM resource envelope.
    # Match Microsoft's solution-flow samples: properties + schemaVersion.
    flow = {
        "properties": {
            "definition": copy.deepcopy(legacy_flow["properties"]["definition"]),
            "templateName": None,
        },
        "schemaVersion": "1.0.0.0",
    }
    flow["properties"]["connectionReferences"] = {
        key: {"runtimeSource": "embedded",
              "connection": {"connectionReferenceLogicalName": logical_name},
              "api": {"name": key}}
        for key, (logical_name, _) in REFERENCES.items()
    }
    workflow_name = "SaveInvoicePDFs-" + flow_id.upper() + ".json"
    workflows_path = SOURCE / "Workflows"
    workflows_path.mkdir(exist_ok=True)
    (workflows_path / workflow_name).write_text(json.dumps(flow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for name in ("Solution.xml", "Customizations.xml"):
        path = SOURCE / "Other" / name
        backup = path.with_suffix(".xml.before-invoice-flow.bak")
        if not backup.exists():
            shutil.copy2(path, backup)

    solution_path = SOURCE / "Other/Solution.xml"
    solution = ET.parse(solution_path)
    manifest = solution.getroot().find("SolutionManifest")
    manifest.find("Version").text = VERSION
    manifest.find("Managed").text = "0"
    manifest.find("LocalizedNames/LocalizedName").set("description", "Invoice PDF automation")
    descriptions = manifest.find("Descriptions")
    descriptions.clear()
    ET.SubElement(descriptions, "Description", {
        "description": "Save invoice and credit-note PDFs to OneDrive, including numbered Faktura_ PDFs when the mail subject contains fra inco.", "languagecode": "1033",
    })
    components = manifest.find("RootComponents")
    components.clear()
    ET.SubElement(components, "RootComponent", {"type": "29", "id": "{" + flow_id + "}", "behavior": "0"})
    xml_save(solution, solution_path)

    customization_path = SOURCE / "Other/Customizations.xml"
    customizations = ET.parse(customization_path)
    root = customizations.getroot()
    workflows = root.find("Workflows")
    workflows.clear()
    workflow = ET.Element("Workflow", {"WorkflowId": "{" + flow_id + "}", "Name": TITLE})
    fields = {
        "JsonFileName": "/Workflows/" + workflow_name,
        "Type": "1", "Subprocess": "0", "Category": "5", "Mode": "0", "Scope": "4",
        "OnDemand": "0", "TriggerOnCreate": "0", "TriggerOnDelete": "0",
        "AsyncAutoDelete": "0", "SyncWorkflowLogOnFailure": "0",
        "StateCode": "0", "StatusCode": "1",  # Dataverse workflow: Draft, not activated.
        "RunAs": "1", "IsTransacted": "1", "IntroducedVersion": "1.0.0.0",
        "IsCustomizable": "1", "BusinessProcessType": "0",
        "IsCustomProcessingStepAllowedForOtherPublishers": "1", "PrimaryEntity": "none",
    }
    for key, value in fields.items():
        ET.SubElement(workflow, key).text = value
    ET.SubElement(ET.SubElement(workflow, "LocalizedNames"), "LocalizedName", {
        "languagecode": "1033", "description": TITLE,
    })
    # Current PAC source format keeps metadata beside JSON; it merges this into
    # customizations.xml inside the ZIP. Keep the source Workflows element empty.
    xml_save(ET.ElementTree(workflow), workflows_path / (workflow_name + ".data.xml"))
    references = root.find("connectionreferences")
    if references is None:
        references = ET.SubElement(root, "connectionreferences")
    references.clear()
    for key, (logical_name, display_name) in REFERENCES.items():
        reference = ET.SubElement(references, "connectionreference", {"connectionreferencelogicalname": logical_name})
        for field, value in {
            "connectionreferencedisplayname": display_name,
            "connectorid": "/providers/Microsoft.PowerApps/apis/" + key,
            "iscustomizable": "1", "statecode": "0", "statuscode": "1",
        }.items():
            ET.SubElement(reference, field).text = value
    xml_save(customizations, customization_path)

    subprocess.run([str(PAC), "solution", "pack", "--folder", str(SOURCE),
                    "--zipfile", str(TARGET), "--packagetype", "Unmanaged"], check=True)
    validate()


def validate_clientdata(flow):
    assert flow.get("schemaVersion") == "1.0.0.0", "Required Dataverse clientdata schemaVersion is missing or invalid"
    assert set(flow) == {"properties", "schemaVersion"}, "Legacy ARM resource envelope must not be used as Dataverse clientdata"
    assert set(flow["properties"]) == {"definition", "connectionReferences", "templateName"}
    assert flow["properties"]["templateName"] is None


def validate():
    with ZipFile(TARGET) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert {"solution.xml", "customizations.xml", "[Content_Types].xml"}.issubset(names)
        solution = ET.fromstring(archive.read("solution.xml"))
        assert solution.findtext("SolutionManifest/UniqueName") == "InvoiceAutomation"
        assert solution.findtext("SolutionManifest/Managed") == "0"
        assert solution.findtext("SolutionManifest/Version") == VERSION
        customs = ET.fromstring(archive.read("customizations.xml"))
        workflow, = customs.findall("Workflows/Workflow")
        assert workflow.findtext("Category") == "5"
        assert workflow.findtext("StateCode") == "0" and workflow.findtext("StatusCode") == "1"
        file = workflow.findtext("JsonFileName").lstrip("/")
        flow = json.loads(archive.read(file))
        validate_clientdata(flow)
        missing_version = copy.deepcopy(flow)
        missing_version.pop("schemaVersion")
        try:
            validate_clientdata(missing_version)
        except AssertionError:
            pass
        else:
            raise AssertionError("Regression check failed: missing schemaVersion was accepted")
        references = {entry.get("connectionreferencelogicalname"): entry
                      for entry in customs.findall("connectionreferences/connectionreference")}
        assert len(references) == 2
        for key, value in flow["properties"]["connectionReferences"].items():
            logical_name = value["connection"]["connectionReferenceLogicalName"]
            assert value["api"]["name"] == key
            assert references[logical_name].findtext("connectorid").endswith("/" + key)
        old_flow = next(value for name, value in make_package().items() if name.endswith("definition.json"))
        assert flow["properties"]["definition"] == old_flow["properties"]["definition"]
    print("PASS: Microsoft CLI packed an unmanaged Dataverse solution.")
    print("PASS: ZIP integrity, flow file, two Dataverse connection references, current tested flow logic, draft state.")
    print("PASS: Dataverse clientdata envelope matches Microsoft's sample; missing schemaVersion is rejected by the regression check.")
    print("NOT VERIFIED: importing into the organization's environment, account permissions or live Outlook/OneDrive execution.")
    print(TARGET)


if __name__ == "__main__":
    main()
