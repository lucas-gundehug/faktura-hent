"""Build a standalone Power Automate import package using only the stdlib."""

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from zipfile import ZIP_DEFLATED, ZipFile

HERE = Path(__file__).resolve().parent
TITLE = "Gem nye fakturaer fra Outlook"


def resource_id(name):
    return str(uuid5(NAMESPACE_URL, "urn:lucas:faktura-hent:v1:" + name))


def connector_inputs(connector, operation, parameters):
    return {
        "host": {
            "apiId": "/providers/Microsoft.PowerApps/apis/" + connector,
            "connectionName": connector,
            "operationId": operation,
        },
        "parameters": parameters,
        "authentication": "@parameters('$authentication')",
    }


def make_package():
    flow_id = resource_id("flow")
    connectors = {
        "shared_office365": "Office 365 Outlook",
        "shared_onedriveforbusiness": "OneDrive for Business",
    }
    filter_expression = (
        "@and("
        "or("
        "and("
        "startsWith(toLower(coalesce(item()?['name'], '')), 'faktura_'), "
        "isInt(slice(toLower(coalesce(item()?['name'], '')), 8, -4)), "
        "contains(toLower(coalesce(triggerBody()?['subject'], '')), 'fra inco')), "
        "startsWith(toLower(coalesce(item()?['name'], '')), 'kreditnota_'), "
        "contains(toLower(coalesce(item()?['name'], '')), 'royal unibrew'), "
        "startsWith(toLower(coalesce(item()?['name'], '')), 'inco')), "
        "endsWith(toLower(coalesce(item()?['name'], '')), '.pdf'), "
        "equals(coalesce(item()?['isInline'], false), false))"
    )
    filename_expression = (
        "@concat("
        "take(replace(replace(replace(replace(replace(replace(replace(replace("
        "replace(replace(replace(replace(trim(substring(items('Save_each_invoice')?['name'], "
        "0, sub(length(items('Save_each_invoice')?['name']), 4))), "
        "'/', '_'), '\\', '_'), ':', '_'), '*', '_'), '?', '_'), "
        "'\"', '_'), '<', '_'), '>', '_'), '|', '_'), '#', '_'), '%', '_'), "
        "decodeUriComponent('%27'), '_'), 120), '_', guid(), '.pdf')"
    )
    definition = {
        "$schema": "https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#",
        "contentVersion": "1.0.0.0",
        "parameters": {
            "$connections": {"defaultValue": {}, "type": "Object"},
            "$authentication": {"defaultValue": {}, "type": "SecureObject"},
        },
        "triggers": {
            "When_a_new_email_arrives_V3": {
                "type": "OpenApiConnectionNotification",
                "inputs": connector_inputs("shared_office365", "OnNewEmailV3", {
                    "folderPath": "Inbox",
                    "includeAttachments": False,
                    "importance": "Any",
                    "fetchOnlyWithAttachment": True,
                }),
                "splitOn": "@triggerOutputs()?['body/value']",
            }
        },
        "actions": {
            "OneDrive_folder": {
                "type": "Compose", "inputs": "/Fakturaer", "runAfter": {},
                "description": "Existing destination folder in OneDrive for Business. Change this value if needed.",
            },
            "Filter_invoice_PDFs": {
                "type": "Query",
                "inputs": {
                    "from": "@coalesce(triggerBody()?['attachments'], json('[]'))",
                    "where": filter_expression,
                },
                "runAfter": {"OneDrive_folder": ["Succeeded"]},
            },
            "Save_each_invoice": {
                "type": "Foreach",
                "foreach": "@body('Filter_invoice_PDFs')",
                "runAfter": {"Filter_invoice_PDFs": ["Succeeded"]},
                "runtimeConfiguration": {"concurrency": {"repetitions": 1}},
                "actions": {
                    "Get_attachment_V2": {
                        "type": "OpenApiConnection", "runAfter": {},
                        "inputs": connector_inputs("shared_office365", "GetAttachment_V2", {
                            "messageId": "@triggerBody()?['id']",
                            "attachmentId": "@items('Save_each_invoice')?['id']",
                        }),
                    },
                    "Unique_file_name": {
                        "type": "Compose", "inputs": filename_expression,
                        "runAfter": {"Get_attachment_V2": ["Succeeded"]},
                        "description": "Keep the invoice name (up to 120 characters) and add a UUID so separate emails do not collide. A resubmitted run creates another copy.",
                    },
                    "Create_file_in_OneDrive": {
                        "type": "OpenApiConnection",
                        "runAfter": {"Unique_file_name": ["Succeeded"]},
                        "inputs": connector_inputs("shared_onedriveforbusiness", "CreateFile", {
                            "folderPath": "@outputs('OneDrive_folder')",
                            "name": "@outputs('Unique_file_name')",
                            "body": "@base64ToBinary(body('Get_attachment_V2')?['contentBytes'])",
                        }),
                        "runtimeConfiguration": {"contentTransfer": {"transferMode": "Chunked"}},
                    },
                },
            },
        },
        "outputs": {},
    }
    flow = {
        "name": flow_id,
        "id": "/providers/Microsoft.Flow/flows/" + flow_id,
        "type": "Microsoft.Flow/flows",
        "properties": {
            "apiId": "/providers/Microsoft.PowerApps/apis/shared_logicflows",
            "displayName": TITLE,
            "description": "Save new invoice PDFs matching Kreditnota_, Royal Unibrew or inco, plus Faktura_<number>.pdf when the subject contains fra inco. Case-insensitive; inline attachments excluded; unique file names.",
            "definition": definition,
            "connectionReferences": {
                key: {
                    "connectionName": key + "-select-at-import",
                    "source": "Embedded",
                    "id": "/providers/Microsoft.PowerApps/apis/" + key,
                    "tier": "NotSpecified",
                } for key in connectors
            },
            "flowFailureAlertSubscribed": False,
        },
    }
    resources = {
        flow_id: {
            "type": "Microsoft.Flow/flows",
            "suggestedCreationType": "New",
            "creationType": "Existing, New, Update",
            "details": {"displayName": TITLE},
            "configurableBy": "User", "hierarchy": "Root",
            "dependsOn": [resource_id(key + suffix) for key in connectors for suffix in (":api", ":connection")],
        }
    }
    for key, label in connectors.items():
        api_id, connection_id = resource_id(key + ":api"), resource_id(key + ":connection")
        resources[api_id] = {
            "id": "/providers/Microsoft.PowerApps/apis/" + key,
            "name": key, "type": "Microsoft.PowerApps/apis",
            "suggestedCreationType": "Existing",
            "details": {"displayName": label},
            "configurableBy": "System", "hierarchy": "Child", "dependsOn": [],
        }
        resources[connection_id] = {
            "type": "Microsoft.PowerApps/apis/connections",
            "suggestedCreationType": "Existing", "creationType": "Existing",
            "details": {"displayName": "Select your " + label + " account"},
            "configurableBy": "User", "hierarchy": "Child", "dependsOn": [api_id],
        }
    base = "Microsoft.Flow/flows/" + flow_id + "/"
    return {
        "manifest.json": {
            "schema": "1.0",
            "details": {
                "displayName": TITLE,
                "description": "New invoice PDF attachments to OneDrive for Business. Select both accounts at import.",
                "createdTime": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "packageTelemetryId": resource_id("package"),
                "creator": "N/A", "sourceEnvironment": "",
            },
            "resources": resources,
        },
        "Microsoft.Flow/flows/manifest.json": {
            "packageSchemaVersion": "1.0", "flowAssets": {"assetPaths": [flow_id]},
        },
        base + "definition.json": flow,
        base + "apisMap.json": {key: resource_id(key + ":api") for key in connectors},
        base + "connectionsMap.json": {key: resource_id(key + ":connection") for key in connectors},
    }


def main():
    documents = make_package()
    for name, document in documents.items():
        target = HERE / "package" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    target_zip = HERE / "build-input" / "legacy-flow-package.zip"
    with ZipFile(target_zip, "w", ZIP_DEFLATED) as archive:
        for name, document in documents.items():
            archive.writestr(name, json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    print(target_zip)


if __name__ == "__main__":
    main()
