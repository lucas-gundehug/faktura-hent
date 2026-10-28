"""Exercise emitted WDL locally; Microsoft connector behavior still needs a live test."""
import base64
import copy
import json
import re
import unittest
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, unquote, urlparse, parse_qs
from uuid import uuid4

from validate_package import parse
try:
    from build_shared_flow import make_shared_package as make_package
except ImportError:
    from build_package import make_package


class ConnectorError(Exception):
    def __init__(self, status):
        self.status = status


class LocalRuntime:
    """Interpret the generated actions, replacing only remote connector calls."""
    def __init__(self, files=None, messages=None, now="2026-10-07T12:00:00Z"):
        self.definition = next(v for k, v in make_package().items()
                               if k.endswith('definition.json'))['properties']['definition']
        self.files = files if files is not None else {}
        self.messages = messages or []
        self.now = now
        self.variables, self.results, self.items = {}, {}, {}
        self.current_item = None
        self.page_size = 1
        self.fail_operation = None
        self.fail_after_pdf = False
        self.archive_count = 1
        self.incomplete_file_listing = False
        self.bad_next_link = False
        self.requests = []

    def expr(self, value):
        if isinstance(value, dict):
            return {k: self.expr(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.expr(v) for v in value]
        if not isinstance(value, str) or not value.startswith('@'):
            return value
        return self.node(parse(value))

    def node(self, node):
        if node[0] == 'literal':
            return node[1]
        if node[0] == 'lookup':
            obj, key = self.node(node[1]), self.node(node[2])
            return obj[key] if isinstance(obj, list) else obj.get(key) if obj is not None else None
        name, args = node[1], node[2]
        if name == 'if':
            return self.node(args[1] if self.node(args[0]) else args[2])
        def union(*arrays):
            result = []
            for arr in arrays:
                for item in arr:
                    if item not in result:
                        result.append(item)
            return result
        def local_date(text, src, dest, fmt):
            assert (src, dest, fmt) == ('UTC', 'Romance Standard Time', 'yyyy-MM-dd')
            dt = datetime.fromisoformat(text.replace('Z', '+00:00'))
            # Independent fixture dates: Copenhagen winter UTC+1; summer UTC+2.
            offset = 2 if 4 <= dt.month <= 10 else 1
            return (dt + timedelta(hours=offset)).strftime('%Y-%m-%d')
        functions = {
            'variables': lambda key: self.variables[key],
            'outputs': lambda key: self.results[key],
            'body': lambda key: self.results[key]['body'],
            'items': lambda key: self.items[key], 'item': lambda: self.current_item,
            'and': lambda *xs: all(xs), 'or': lambda *xs: any(xs),
            'not': lambda x: not x, 'equals': lambda a, b: a == b,
            'greater': lambda a, b: a > b, 'greaterOrEquals': lambda a, b: a >= b,
            'less': lambda a, b: a < b, 'empty': lambda x: not x,
            'contains': lambda a, b: b in a, 'startsWith': str.startswith,
            'endsWith': str.endswith, 'toLower': str.lower, 'trim': str.strip,
            'coalesce': lambda *xs: next((x for x in xs if x is not None), None),
            'length': len, 'first': lambda x: x[0], 'concat': lambda *xs: ''.join(xs),
            'json': json.loads, 'string': lambda x: json.dumps(x, separators=(',', ':')),
            'createArray': lambda *xs: list(xs), 'union': union,
            'sub': lambda a, b: a - b, 'mul': lambda a, b: a * b,
            'div': lambda a, b: a // b, 'add': lambda a, b: a + b,
            'slice': lambda s, a, b: s[a:b], 'take': lambda s, n: s[:n],
            'substring': lambda s, a, n: s[a:a+n], 'replace': str.replace,
            'isInt': lambda s: bool(re.fullmatch(r'[+-]?[0-9]+', s.strip())),
            'guid': lambda: str(uuid4()), 'utcNow': lambda: self.now,
            'encodeUriComponent': lambda s: quote(s, safe=''),
            'decodeUriComponent': unquote,
            'base64ToBinary': base64.b64decode,
            'base64ToString': lambda s: base64.b64decode(s).decode(),
            'convertTimeZone': local_date,
        }
        return functions[name](*(self.node(x) for x in args))

    def actions(self, actions):
        statuses = {}
        for name, action in actions.items():
            dependencies = action.get('runAfter', {})
            if any(statuses.get(dep) not in allowed for dep, allowed in dependencies.items()):
                statuses[name] = 'Skipped'
                continue
            try:
                self.action(name, action)
                statuses[name] = 'Succeeded'
            except ConnectorError as error:
                self.results[name] = {'statusCode': error.status, 'body': {'error': 'fixture'}}
                statuses[name] = 'Failed'
        # A handled connector failure is allowed only when a successful dependent handles it.
        failures = [name for name, status in statuses.items() if status == 'Failed'
                    and not any(statuses.get(other) == 'Succeeded' and 'Failed' in spec.get('runAfter', {}).get(name, [])
                                for other, spec in actions.items())]
        if failures:
            raise ConnectorError(500)

    def action(self, name, action):
        typ = action['type']
        if typ == 'Compose':
            self.results[name] = self.expr(action['inputs'])
        elif typ == 'InitializeVariable':
            for var in action['inputs']['variables']:
                self.variables[var['name']] = self.expr(var['value'])
        elif typ in ('SetVariable', 'AppendToArrayVariable', 'IncrementVariable'):
            key, val = action['inputs']['name'], self.expr(action['inputs']['value'])
            if typ == 'SetVariable':
                self.variables[key] = val
            elif typ == 'AppendToArrayVariable':
                self.variables[key].append(copy.deepcopy(val))
            else:
                self.variables[key] += val
        elif typ == 'If':
            self.actions(action.get('actions', {}) if self.expr(action['expression']) else action.get('else', {}).get('actions', {}))
        elif typ == 'Foreach':
            for item in list(self.expr(action['foreach'])):
                self.items[name] = item
                self.actions(action['actions'])
        elif typ == 'Until':
            for _ in range(action['limit']['count']):
                self.actions(action['actions'])
                if self.expr(action['expression']):
                    break
            else:
                raise ConnectorError(408)
        elif typ == 'Scope':
            self.actions(action['actions'])
        elif typ == 'Query':
            values = []
            for item in self.expr(action['inputs']['from']):
                self.current_item = item
                if self.expr(action['inputs']['where']):
                    values.append(item)
            self.results[name] = {'body': values}
        elif typ == 'ParseJson':
            doc = self.expr(action['inputs']['content'])
            if not isinstance(doc, dict) or not isinstance(doc.get('processed'), list) or not isinstance(doc.get('startUtc'), str):
                raise ConnectorError(400)
            datetime.fromisoformat(doc['startUtc'].replace('Z', '+00:00'))
            self.results[name] = {'body': doc}
        elif typ == 'Terminate':
            raise ConnectorError(400)
        elif typ == 'OpenApiConnection':
            params = self.expr(action['inputs']['parameters'])
            op = action['inputs']['host']['operationId']
            if op == self.fail_operation:
                raise ConnectorError(403)
            self.results[name] = {'body': self.connector(op, params), 'statusCode': 200}
        else:
            raise AssertionError(typ)

    def metadata(self, path):
        is_folder = path not in self.files
        return {'Id': path, 'Name': path.rsplit('/', 1)[-1], 'Path': path,
                'IsFolder': is_folder, 'Size': 0 if is_folder else len(self.files[path])}

    def connector(self, op, p):
        self.requests.append((op, p))
        if op == 'HttpRequest':
            assert p['Method'] == 'GET'
            url = urlparse(p['Uri']); query = parse_qs(url.query)
            assert url.netloc == 'graph.microsoft.com'
            path = unquote(url.path)
            assert path.startswith('/v1.0/users/faktura@alcora.dk/')
            if path.endswith('/childFolders') or path.endswith('/mailFolders'):
                vals = [{'id': 'archive', 'displayName': 'Arkiv_10_2024_>'}] * self.archive_count if path.endswith('/mailFolders') else []
            elif path.endswith('/messages'):
                folder = path.split('/')[-2]
                cutoff = re.search(r'receivedDateTime ge (\S+)', query['$filter'][0]).group(1)
                vals = [m for m in self.messages if m['folder'] == folder and m['receivedDateTime'] >= cutoff and m['hasAttachments']]
                vals = sorted(vals, key=lambda m: m['receivedDateTime'])
            elif path.endswith('/attachments'):
                msg = next(m for m in self.messages if m['id'] == path.split('/')[-2])
                vals = msg['attachments']
            else:
                raise AssertionError(path)
            skip = int(query.get('$skip', [0])[0]); page = vals[skip:skip+self.page_size]
            result = {'value': copy.deepcopy(page)}
            if skip + self.page_size < len(vals):
                clean = re.sub(r'&?\$skip=\d+', '', p['Uri'])
                result['@odata.nextLink'] = clean + ('&' if '?' in clean else '?') + '$skip=' + str(skip+self.page_size)
                if self.bad_next_link and path.endswith('/messages'):
                    result['@odata.nextLink'] = 'https://other.example/steal'
            return result
        if op == 'GetAttachment_V2':
            assert p['mailboxAddress'] == 'faktura@alcora.dk'
            msg = next(m for m in self.messages if m['id'] == p['messageId'])
            att = next(a for a in msg['attachments'] if a['id'] == p['attachmentId'])
            return {'contentBytes': base64.b64encode(att['payload']).decode()}
        if op in ('GetFileContentByPath', 'GetFileContent'):
            path = p.get('path', p.get('id'))
            if path not in self.files:
                raise ConnectorError(404)
            return {'$content-type': 'application/octet-stream', '$content': base64.b64encode(self.files[path]).decode()}
        if op == 'GetFileMetadataByPath':
            return self.metadata(p['path'])
        if op == 'ListFolderV2':
            root = p['id'].rstrip('/') + '/'
            children = set()
            for path in self.files:
                if path.startswith(root):
                    children.add(root + path[len(root):].split('/')[0])
            result = {'value': [self.metadata(x) for x in sorted(children)]}
            if self.incomplete_file_listing:
                result['nextLink'] = 'https://connector.example/next'
            return result
        if op in ('CreateFile', 'UpdateFile'):
            path = p['folderPath'].rstrip('/') + '/' + p['name'] if op == 'CreateFile' else p['id']
            value = p['body']; value = value.encode() if isinstance(value, str) else value
            self.files[path] = value
            if self.fail_after_pdf and path.endswith('.pdf'):
                self.fail_after_pdf = False
                raise ConnectorError(504)  # Server write succeeded, client saw timeout.
            return self.metadata(path)
        raise AssertionError(op)

    def run(self):
        self.variables, self.results, self.items = {}, {}, {}
        self.actions(self.definition['actions'])

    def pdfs(self):
        return {p: v for p, v in self.files.items() if p.lower().endswith('.pdf')}


def mail(id, folder='inbox', date='2026-10-07T22:30:00Z', name='inco invoice.pdf', payload=b'%PDF-one', inline=False):
    return {'id': id, 'folder': folder, 'subject': 'Faktura 123 fra inco',
            'receivedDateTime': date, 'hasAttachments': True,
            'attachments': [{'id': id+'-attachment', 'name': name, 'isInline': inline, 'payload': payload}]}


class SharedFlowTests(unittest.TestCase):
    def runtime(self, messages=None):
        runtime = LocalRuntime()
        trigger, = runtime.definition['triggers'].values()
        self.assertEqual(trigger['type'], 'Recurrence', 'Both folders require the new scheduled shared-mailbox flow')
        runtime.run()  # First activation stores the future-only boundary.
        runtime.messages = messages or []
        runtime.now = '2026-10-10T12:00:00Z'
        runtime.run()
        return runtime

    def test_microsoft_action_constraints(self):
        rt = LocalRuntime()
        names = set()
        def visit(actions, depth=0, in_loop=False):
            self.assertLessEqual(depth, 8, 'Power Automate supports at most eight nested actions')
            for name, action in actions.items():
                self.assertNotIn(name, names, 'Action references must be unambiguous')
                names.add(name)
                if action['type'] == 'InitializeVariable':
                    self.assertEqual(len(action['inputs']['variables']), 1)
                    self.assertEqual(depth, 0)
                if action['type'] == 'Terminate':
                    self.assertFalse(in_loop, 'Terminate is forbidden inside Foreach/Until')
                nested_loop = in_loop or action['type'] in ('Foreach', 'Until')
                for branch in (action.get('actions'), action.get('else', {}).get('actions')):
                    if branch:
                        visit(branch, depth+1, nested_loop)
        visit(rt.definition['actions'])

    def test_both_folders_content_dedup_and_danish_date(self):
        rt = self.runtime([mail('a'), mail('b', 'archive'), mail('c', 'archive', payload=b'%PDF-two')])
        self.assertEqual(sorted(rt.pdfs().values()), [b'%PDF-one', b'%PDF-two'])
        self.assertTrue(all(p.startswith('/Fakturaer/2026-10-08/') for p in rt.pdfs()))

    def test_historical_messages_excluded_and_rerun_skips_saved(self):
        rt = self.runtime([mail('old', date='2026-10-01T00:00:00Z'), mail('new')])
        before = dict(rt.pdfs()); rt.run()
        self.assertEqual(rt.pdfs(), before)
        self.assertEqual(list(before.values()), [b'%PDF-one'])

    def test_identical_content_across_dates_and_changed_names(self):
        rt = self.runtime([mail('a'), mail('b', 'archive', '2026-10-09T10:00:00Z', 'Royal Unibrew copy.pdf')])
        self.assertEqual(len(rt.pdfs()), 1)

    def test_filters_and_attachment_paging(self):
        msg = mail('a', name='Faktura_TEST.pdf')
        msg['attachments'] += [mail('b', name='Faktura_123.pdf')['attachments'][0],
                               mail('c', name='inco inline.pdf', inline=True)['attachments'][0]]
        rt = self.runtime([msg])
        self.assertEqual(len(rt.pdfs()), 1)
        self.assertEqual(list(rt.pdfs()), ['/Fakturaer/2026-10-08/Faktura_123.pdf'])

    def test_real_invoice_names_are_kept(self):
        names = ['Faktura_9583195.pdf', 'Kreditnota_K-8063141.pdf',
                 'Faktura fra Royal Unibrew 9009227055 - royal.pdf']
        rt = self.runtime([mail(str(i), name=n, payload=b'%PDF-' + n.encode()) for i, n in enumerate(names)])
        self.assertEqual(sorted(p.split('/')[-1] for p in rt.pdfs()), sorted(names))

    def test_same_name_different_invoice_gets_suffix_not_overwrite(self):
        # inco and Royal name files after the due date, so different invoices share a name.
        name = 'inco Aarhus A_S_29.12.2026.pdf'
        rt = self.runtime([mail('a', name=name, payload=b'%PDF-9579279'),
                           mail('b', 'archive', name=name, payload=b'%PDF-9582301'),
                           mail('c', date='2026-10-09T10:00:00Z', name=name, payload=b'%PDF-9582273')])
        self.assertEqual(sorted(rt.pdfs().values()), [b'%PDF-9579279', b'%PDF-9582273', b'%PDF-9582301'])
        names = [p.split('/')[-1] for p in rt.pdfs()]
        self.assertIn(name, names)
        self.assertEqual(len(set(n.lower() for n in names)), 3)
        self.assertTrue(all(n.startswith('inco Aarhus A_S_29.12.2026') and n.endswith('.pdf') for n in names))

    def test_moved_mail_and_restart_do_not_duplicate(self):
        rt = self.runtime([mail('a')])
        saved = dict(rt.pdfs())
        rt.messages = [mail('moved-id', 'archive')]
        rt.run(); self.assertEqual(rt.pdfs(), saved)
        restarted = LocalRuntime(files=rt.files, messages=rt.messages)
        restarted.run(); self.assertEqual(restarted.pdfs(), saved)

    def test_failed_upload_is_recovered_without_second_pdf(self):
        rt = self.runtime()
        rt.messages = [mail('a')]; rt.fail_after_pdf = True
        with self.assertRaises(ConnectorError):
            rt.run()
        rt.run(); self.assertEqual(len(rt.pdfs()), 1)

    def test_access_error_never_resets_cutoff(self):
        rt = self.runtime()
        before = dict(rt.files); rt.fail_operation = 'GetFileContentByPath'
        with self.assertRaises(ConnectorError):
            rt.run()
        self.assertEqual(rt.files, before)

    def test_missing_archive_fails_instead_of_silent_partial_scan(self):
        rt = self.runtime(); rt.archive_count = 0
        with self.assertRaises(ConnectorError):
            rt.run()

    def test_corrupt_state_is_not_replaced(self):
        rt = self.runtime()
        path = '/Fakturaer/_automation-state.json'; rt.files[path] = b'broken'
        with self.assertRaises((ConnectorError, ValueError)):
            rt.run()
        self.assertEqual(rt.files[path], b'broken')

    def test_partial_file_index_cannot_allow_duplicate_upload(self):
        rt = self.runtime(); rt.messages = [mail('a')]; rt.incomplete_file_listing = True
        with self.assertRaises(ConnectorError):
            rt.run()
        self.assertEqual(rt.pdfs(), {})

    def test_pagination_url_cannot_leave_microsoft_mailbox(self):
        rt = self.runtime(); rt.messages = [mail('a'), mail('b')]; rt.bad_next_link = True
        with self.assertRaises(ConnectorError):
            rt.run()
        self.assertEqual(rt.pdfs(), {})

    def test_binary_length_with_all_base64_padding_variants(self):
        for payload in (b'1234', b'12345', b'123456'):
            with self.subTest(payload=payload):
                rt = self.runtime([mail('a', payload=payload), mail('b', 'archive', payload=payload)])
                self.assertEqual(list(rt.pdfs().values()), [payload])

    def test_existing_root_pdf_is_not_copied_into_date_folder(self):
        rt = self.runtime(); rt.files['/Fakturaer/inco_old_uuid.pdf'] = b'%PDF-one'
        rt.messages = [mail('a')]; rt.run()
        self.assertEqual(rt.pdfs(), {'/Fakturaer/inco_old_uuid.pdf': b'%PDF-one'})

    def test_winter_date_uses_danish_received_time(self):
        rt = self.runtime([mail('a', date='2027-01-10T23:30:00Z')])
        self.assertTrue(next(iter(rt.pdfs())).startswith('/Fakturaer/2027-01-11/'))


if __name__ == '__main__':
    unittest.main()
