"""Repack the Microsoft-generated v4 container, preserving identity/connections."""
import json
import xml.etree.ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

from build_package import HERE
from build_shared_flow import definition
from build_solution import validate_clientdata

SOURCE = HERE / 'build-input' / 'solution-container-v4.zip'
TARGET = HERE / 'Gem-nye-fakturaer-solution-v5.zip'
VERSION = '1.0.0.5'


def main():
    with ZipFile(SOURCE) as archive:
        assert archive.testzip() is None
        documents = {name: archive.read(name) for name in archive.namelist()}
    original = dict(documents)
    solution = ET.fromstring(documents['solution.xml'])
    assert solution.findtext('SolutionManifest/UniqueName') == 'InvoiceAutomation'
    solution.find('SolutionManifest/Version').text = VERSION
    solution.find('SolutionManifest/Descriptions/Description').set('description',
        'Future shared-mailbox invoice PDFs in Copenhagen date folders, with exact content deduplication.')
    customs = ET.fromstring(documents['customizations.xml'])
    workflow, = customs.findall('Workflows/Workflow')
    assert workflow.findtext('StateCode') == '0', 'Import as draft, review before activation'
    workflow_path = workflow.findtext('JsonFileName').lstrip('/')
    flow = json.loads(documents[workflow_path])
    validate_clientdata(flow)
    flow['properties']['definition'] = definition()
    documents[workflow_path] = (json.dumps(flow, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    documents['solution.xml'] = ET.tostring(solution, encoding='utf-8', xml_declaration=True)
    with ZipFile(TARGET, 'w', ZIP_DEFLATED) as archive:
        for name, content in documents.items():
            archive.writestr(name, content)
    for name, content in documents.items():
        path = HERE / 'solution-v5' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    with ZipFile(TARGET) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == set(original)
        assert {name for name in original if original[name] != archive.read(name)} == {'solution.xml', workflow_path}
        packed_flow = json.loads(archive.read(workflow_path))
        validate_clientdata(packed_flow)
        assert packed_flow['properties']['definition'] == definition()
        assert ET.fromstring(archive.read('solution.xml')).findtext('SolutionManifest/Version') == VERSION
        refs = packed_flow['properties']['connectionReferences']
        assert len(refs) == 2
        logical_names = {node.get('connectionreferencelogicalname')
                         for node in customs.findall('connectionreferences/connectionreference')}
        assert {r['connection']['connectionReferenceLogicalName'] for r in refs.values()} == logical_names
    print('PASS: v5 ZIP/JSON/XML integrity, draft workflow and original flow identity/connections.')
    print('PASS: only solution version/description and workflow logic differ from the Microsoft-generated v4 container.')
    print('NOT VERIFIED: Microsoft tenant import, shared-mailbox permissions, folder creation or live execution.')
    print(TARGET)


if __name__ == '__main__':
    main()
