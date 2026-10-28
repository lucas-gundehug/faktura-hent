"""Repack the Microsoft-generated v4 container under its own solution identity.

The v4 solution (and its connection references) may already exist in the
environment, owned by another user. Importing the same identities would try to
update their records and fail with a WriteAccess error, so this package gets its
own solution, publisher, workflow ID and connection references.
"""
import json
import shutil
import xml.etree.ElementTree as ET
from uuid import NAMESPACE_URL, uuid5
from zipfile import ZIP_DEFLATED, ZipFile

from build_package import HERE
from build_shared_flow import definition
from build_solution import validate_clientdata

SOURCE = HERE / 'build-input' / 'solution-container-v4.zip'
TARGET = HERE / 'Gem-nye-fakturaer-solution-v5.zip'
VERSION = '1.0.0.6'

OLD_WORKFLOW_ID = 'd3c10c43-c4de-5b70-8360-0735390b41e1'
WORKFLOW_ID = str(uuid5(NAMESPACE_URL, 'urn:lucas:faktura-hent:shared-mailbox:workflow'))
FLOW_NAME = 'Gem fakturaer fra faktura@alcora.dk'
OLD_REFERENCES = ('lgi_invoice_outlook', 'lgi_invoice_onedrive')
REFERENCES = ('fkh_faktura_outlook', 'fkh_faktura_onedrive')

# Exact-text replacements; each must occur in the source container.
SOLUTION_XML = [
    ('<UniqueName>InvoiceAutomation</UniqueName>', '<UniqueName>FakturaHentSharedMailbox</UniqueName>'),
    ('description="Invoice PDF automation"', 'description="Faktura-hent (shared mailbox)"'),
    ('<UniqueName>LucasInvoiceAutomation</UniqueName>', '<UniqueName>FakturaHent</UniqueName>'),
    ('description="LucasInvoiceAutomation"', 'description="Faktura-hent"'),
    ('<CustomizationPrefix>lgi</CustomizationPrefix>', '<CustomizationPrefix>fkh</CustomizationPrefix>'),
    ('<CustomizationOptionValuePrefix>78621</CustomizationOptionValuePrefix>',
     '<CustomizationOptionValuePrefix>43817</CustomizationOptionValuePrefix>'),
    (OLD_WORKFLOW_ID, WORKFLOW_ID),
]
CUSTOMIZATIONS_XML = [
    (OLD_WORKFLOW_ID, WORKFLOW_ID),
    (OLD_WORKFLOW_ID.upper(), WORKFLOW_ID.upper()),
    ('"Gem nye fakturaer fra Outlook"', '"' + FLOW_NAME + '"'),
    *zip(('"' + r + '"' for r in OLD_REFERENCES), ('"' + r + '"' for r in REFERENCES)),
    ('Invoice automation - Outlook', 'Faktura-hent - Outlook'),
    ('Invoice automation - OneDrive', 'Faktura-hent - OneDrive'),
]
OLD_IDENTITIES = [OLD_WORKFLOW_ID, 'LucasInvoiceAutomation', '>InvoiceAutomation<', 'lgi_', 'Invoice automation -']


def rewrite(text, replacements):
    for old, new in replacements:
        assert old in text, f'Expected identity not found in source container: {old}'
        text = text.replace(old, new)
    return text


def main():
    with ZipFile(SOURCE) as archive:
        assert archive.testzip() is None
        documents = {name: archive.read(name) for name in archive.namelist()}
    original_names = set(documents)
    solution = ET.fromstring(documents['solution.xml'])
    assert solution.findtext('SolutionManifest/UniqueName') == 'InvoiceAutomation'
    solution.find('SolutionManifest/Version').text = VERSION
    solution.find('SolutionManifest/Descriptions/Description').set('description',
        'Future shared-mailbox invoice PDFs in Copenhagen date folders, with exact content deduplication.')
    customs = ET.fromstring(documents['customizations.xml'])
    workflow, = customs.findall('Workflows/Workflow')
    assert workflow.findtext('StateCode') == '0', 'Import as draft, review before activation'
    old_path = workflow.findtext('JsonFileName').lstrip('/')
    workflow_path = old_path.replace(OLD_WORKFLOW_ID.upper(), WORKFLOW_ID.upper())
    flow = json.loads(documents.pop(old_path))
    validate_clientdata(flow)
    flow['properties']['definition'] = definition()
    for ref, old, new in zip(flow['properties']['connectionReferences'].values(), OLD_REFERENCES, REFERENCES):
        assert ref['connection']['connectionReferenceLogicalName'] == old
        ref['connection']['connectionReferenceLogicalName'] = new
    documents[workflow_path] = (json.dumps(flow, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    documents['solution.xml'] = rewrite(
        ET.tostring(solution, encoding='unicode', xml_declaration=True), SOLUTION_XML).encode('utf-8')
    documents['customizations.xml'] = rewrite(
        documents['customizations.xml'].decode('utf-8-sig'), CUSTOMIZATIONS_XML).encode('utf-8-sig')

    with ZipFile(TARGET, 'w', ZIP_DEFLATED) as archive:
        for name, content in documents.items():
            archive.writestr(name, content)
    out_dir = (HERE / 'solution-v5').resolve()
    shutil.rmtree(out_dir, ignore_errors=True)
    for name, content in documents.items():
        path = (out_dir / name).resolve()
        assert path.is_relative_to(out_dir), f'Unsafe archive path: {name}'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    with ZipFile(TARGET) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == original_names - {old_path} | {workflow_path}
        for name in archive.namelist():
            text = archive.read(name).decode('utf-8-sig')
            for old in OLD_IDENTITIES:
                assert old.lower() not in text.lower(), f'{name} still contains {old}'
        packed_flow = json.loads(archive.read(workflow_path))
        validate_clientdata(packed_flow)
        assert packed_flow['properties']['definition'] == definition()
        packed_solution = ET.fromstring(archive.read('solution.xml'))
        assert packed_solution.findtext('SolutionManifest/Version') == VERSION
        assert packed_solution.find('SolutionManifest/RootComponents/RootComponent').get('id') == '{' + WORKFLOW_ID + '}'
        packed_customs = ET.fromstring(archive.read('customizations.xml'))
        packed_workflow, = packed_customs.findall('Workflows/Workflow')
        assert packed_workflow.get('WorkflowId') == '{' + WORKFLOW_ID + '}'
        assert packed_workflow.findtext('JsonFileName') == '/' + workflow_path
        logical_names = {node.get('connectionreferencelogicalname')
                         for node in packed_customs.findall('connectionreferences/connectionreference')}
        refs = packed_flow['properties']['connectionReferences']
        assert {r['connection']['connectionReferenceLogicalName'] for r in refs.values()} == logical_names == set(REFERENCES)
    print('PASS: v5 ZIP/JSON/XML integrity, draft workflow, connection references match.')
    print('PASS: own solution, publisher, workflow ID and connection references; no v4 identity left.')
    print('NOT VERIFIED: Microsoft tenant import, shared-mailbox permissions, folder creation or live execution.')
    print(TARGET)


if __name__ == '__main__':
    main()
