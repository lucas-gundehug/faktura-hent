"""v5: scheduled two-folder shared mailbox, Copenhagen dates and content dedup."""
import copy
from build_package import connector_inputs, make_package, resource_id


def compose(value):
    return {'type': 'Compose', 'inputs': value, 'runAfter': {}}


def variable(name, value, kind='SetVariable'):
    return {'type': kind, 'inputs': {'name': name, 'value': value}, 'runAfter': {}}


def sequence(**actions):
    previous = None
    for name, action in actions.items():
        action['runAfter'] = {previous: ['Succeeded']} if previous else {}
        previous = name
    return actions


def api(connector, operation, parameters):
    return {'type': 'OpenApiConnection', 'inputs': connector_inputs(connector, operation, parameters), 'runAfter': {}}


def http(uri):
    return api('shared_office365', 'HttpRequest', {'Uri': uri, 'Method': 'GET', 'ContentType': 'application/json'})


def drive(operation, **parameters):
    return api('shared_onedriveforbusiness', operation, parameters)


def condition(expression, actions, otherwise=None):
    return {'type': 'If', 'expression': expression, 'actions': actions,
            'else': {'actions': otherwise or {}}, 'runAfter': {}}


def fail(code, message):
    return {'type': 'Terminate', 'inputs': {'runStatus': 'Failed',
            'runError': {'code': code, 'message': message}}, 'runAfter': {}}


def foreach(expression, actions):
    return {'type': 'Foreach', 'foreach': expression, 'actions': actions, 'runAfter': {},
            'runtimeConfiguration': {'concurrency': {'repetitions': 1}}}


def until(expression, actions):
    scope_name = next(iter(actions)) + '_iteration'
    guarded = {
        scope_name: {'type': 'Scope', 'actions': actions, 'runAfter': {}},
        scope_name + '_failed': {
            **variable('Fatal_error', True),
            'runAfter': {scope_name: ['Failed', 'TimedOut']},
        },
    }
    return {'type': 'Until', 'expression': '@or(' + expression[1:] + ", variables('Fatal_error'))", 'actions': guarded,
            'limit': {'count': 5000, 'timeout': 'PT1H'},
            'operationOptions': 'FailWhenLimitsReached', 'runAfter': {}}


def append(name, value):
    return variable(name, value, 'AppendToArrayVariable')


def state_document():
    return compose({'schemaVersion': 1, 'startUtc': "@variables('Start_utc')",
                    'processed': "@variables('Processed_messages')"})


def checked_http(name, uri, remainder):
    """Never follow a malformed nextLink outside the selected Microsoft mailbox."""
    return condition("@and(equals(variables('Fatal_error'), false), startsWith(" + uri[1:] + ", outputs('Mailbox_base')))",
                     sequence(**{name: http(uri), **remainder}),
                     {name + '_reject_invalid_URL': variable('Fatal_error', True)})


def definition():
    legacy = next(v for k, v in make_package().items() if k.endswith('definition.json'))['properties']['definition']
    filter_expression = legacy['actions']['Filter_invoice_PDFs']['inputs']['where'].replace(
        "triggerBody()", "items('For_each_mail')")
    filename = legacy['actions']['Save_each_invoice']['actions']['Unique_file_name']['inputs']
    # Invoices already carry unique names, so keep the sanitized original name.
    # Identical content is skipped earlier; a different PDF with a name already
    # under the destination gets a short suffix instead of overwriting it.
    guid_suffix = ", '_', guid(), '.pdf')"
    assert filename.startswith('@concat(') and filename.endswith(guid_suffix)
    clean_stem = '@' + filename[len('@concat('):-len(guid_suffix)]

    # The destination is listed directly (no search-index lag). Recovering after
    # a PDF upload succeeds but state persistence fails rebuilds this same index.
    list_files = drive('ListFolderV2', id="@variables('Folders_to_scan')[variables('Folder_index')]")
    list_files['runtimeConfiguration'] = {'paginationPolicy': {'minimumItemCount': 5000}}
    index_files = sequence(
        Get_destination_metadata=drive('GetFileMetadataByPath', path="@outputs('OneDrive_folder')"),
        Add_destination_to_queue=append('Folders_to_scan', "@body('Get_destination_metadata')?['Id']"),
        Walk_destination_folders=until(
            "@greaterOrEquals(variables('Folder_index'), length(variables('Folders_to_scan')))",
            sequence(
                List_destination_children=list_files,
                Require_complete_file_listing=condition(
                    "@or(not(empty(body('List_destination_children')?['nextLink'])), greaterOrEquals(length(body('List_destination_children')?['value']), 5000))",
                    {'Stop_incomplete_file_listing': variable('Fatal_error', True)}),
                Index_each_child=foreach("@body('List_destination_children')?['value']", {
                    'Folder_or_PDF': condition("@equals(items('Index_each_child')?['IsFolder'], true)",
                        {'Queue_subfolder': append('Folders_to_scan', "@items('Index_each_child')?['Id']")},
                        {'Index_PDF_only': condition("@endsWith(toLower(coalesce(items('Index_each_child')?['Name'], '')), '.pdf')",
                            {'Add_existing_PDF': append('Existing_PDFs', "@items('Index_each_child')")})}),
                }),
                Advance_folder_index=variable('Folder_index', 1, 'IncrementVariable'),
            )),
    )

    # Compute decoded byte length from canonical base64, rather than trusting
    # Exchange attachment size (which may include attachment metadata).
    bytes_expr = (
        "@sub(div(mul(length(body('Get_attachment_V2')?['contentBytes']), 3), 4), "
        "if(endsWith(body('Get_attachment_V2')?['contentBytes'], '=='), 2, "
        "if(endsWith(body('Get_attachment_V2')?['contentBytes'], '='), 1, 0)))"
    )
    create_pdf = drive('CreateFile', folderPath="@outputs('Received_date_folder')",
                       name="@outputs('Unique_file_name')",
                       body="@base64ToBinary(body('Get_attachment_V2')?['contentBytes'])")
    create_pdf['runtimeConfiguration'] = {'contentTransfer': {'transferMode': 'Chunked'}}
    save_pdf = sequence(
        Get_attachment_V2=api('shared_office365', 'GetAttachment_V2', {
            'messageId': "@items('For_each_mail')?['id']",
            'attachmentId': "@items('Save_each_invoice')?['id']",
            'mailboxAddress': "@outputs('Shared_mailbox')",
        }),
        Reset_duplicate_flag=variable('Duplicate_found', False),
        PDF_byte_length=compose(bytes_expr),
        Same_size_PDFs={'type': 'Query', 'inputs': {'from': "@variables('Existing_PDFs')",
            'where': "@equals(item()?['Size'], outputs('PDF_byte_length'))"}, 'runAfter': {}},
        Compare_saved_PDFs=foreach("@body('Same_size_PDFs')", {
            'Compare_until_match': condition("@equals(variables('Duplicate_found'), false)", sequence(
                Read_existing_PDF=drive('GetFileContent', id="@items('Compare_saved_PDFs')?['Id']", inferContentType=False),
                Match_PDF_content=condition("@equals(body('Read_existing_PDF')?['$content'], body('Get_attachment_V2')?['contentBytes'])",
                    {'Remember_duplicate': variable('Duplicate_found', True)}),
            )),
        }),
        Save_only_new_content=condition("@equals(variables('Duplicate_found'), false)", sequence(
            Received_date_folder=compose("@concat(outputs('OneDrive_folder'), '/', convertTimeZone(items('For_each_mail')?['receivedDateTime'], 'UTC', 'Romance Standard Time', 'yyyy-MM-dd'))"),
            Clean_file_stem=compose(clean_stem),
            Same_name_PDFs={'type': 'Query', 'inputs': {'from': "@variables('Existing_PDFs')",
                'where': "@equals(toLower(coalesce(item()?['Name'], '')), toLower(concat(outputs('Clean_file_stem'), '.pdf')))"}, 'runAfter': {}},
            Unique_file_name=compose("@if(empty(body('Same_name_PDFs')), concat(outputs('Clean_file_stem'), '.pdf'), concat(outputs('Clean_file_stem'), '_', take(guid(), 8), '.pdf'))"),
            Create_file_in_OneDrive=create_pdf,
            Add_new_PDF_to_index=append('Existing_PDFs', "@body('Create_file_in_OneDrive')"),
        )),
    )

    process_mail = sequence(
        Set_attachment_page_URL=variable('Attachment_page_URL',
            "@concat(outputs('Mailbox_base'), 'messages/', encodeUriComponent(items('For_each_mail')?['id']), '/attachments?$select=id,name,isInline,size&$top=100')"),
        Read_all_attachment_pages=until("@empty(variables('Attachment_page_URL'))", {
            'Validate_attachment_page_URL': checked_http('Get_attachment_page', "@variables('Attachment_page_URL')", {
                'Filter_invoice_PDFs': {'type': 'Query', 'inputs': {
                    'from': "@body('Get_attachment_page')?['value']", 'where': filter_expression}, 'runAfter': {}},
                'Save_each_invoice': foreach("@body('Filter_invoice_PDFs')", save_pdf),
                'Next_attachment_page': variable('Attachment_page_URL', "@coalesce(body('Get_attachment_page')?['@odata.nextLink'], '')"),
            }),
        }),
        Persist_only_complete_mail=condition("@equals(variables('Fatal_error'), false)", sequence(
            Remember_processed_mail=append('Processed_messages', "@items('For_each_mail')?['id']"),
            Updated_state_document=state_document(),
            Persist_processed_mail=drive('UpdateFile', id="@variables('State_file_ID')", body="@string(outputs('Updated_state_document'))"),
        )),
    )

    new_state = sequence(
        Initial_state_document=state_document(),
        Create_state_file=drive('CreateFile', folderPath="@outputs('OneDrive_folder')",
                               name='_automation-state.json', body="@string(outputs('Initial_state_document'))"),
        Remember_new_state_ID=variable('State_file_ID', "@body('Create_state_file')?['Id']"),
    )
    existing_state = sequence(
        Parse_saved_state={'type': 'ParseJson',
            'inputs': {'content': "@json(base64ToString(body('Read_saved_state')?['$content']))",
                       'schema': {'type': 'object', 'required': ['schemaVersion', 'startUtc', 'processed'],
                           'properties': {'schemaVersion': {'type': 'integer', 'enum': [1]},
                                          'startUtc': {'type': 'string', 'format': 'date-time'},
                                          'processed': {'type': 'array', 'items': {'type': 'string'}}}}}, 'runAfter': {}},
        Load_start_time=variable('Start_utc', "@body('Parse_saved_state')?['startUtc']"),
        Load_processed_mails=variable('Processed_messages', "@body('Parse_saved_state')?['processed']"),
        Get_state_metadata=drive('GetFileMetadataByPath', path="@concat(outputs('OneDrive_folder'), '/_automation-state.json')"),
        Load_state_ID=variable('State_file_ID', "@body('Get_state_metadata')?['Id']"),
    )
    load_state = condition("@equals(outputs('Read_saved_state')?['statusCode'], 200)", existing_state, {
        'Only_initialize_on_404': condition("@equals(outputs('Read_saved_state')?['statusCode'], 404)",
            new_state, {'Stop_state_read_error': fail('StateReadFailed', 'Cannot read the state file. Start time has not been reset.')}),
    })

    def folder_request(parent):
        return "@concat(outputs('Mailbox_base'), '" + parent + "?$filter=', encodeUriComponent(concat('displayName eq ', decodeUriComponent('%27'), outputs('Archive_folder_name'), decodeUriComponent('%27'))), '&$select=id,displayName&$top=1000')"

    actions = sequence(
        Shared_mailbox=compose('faktura@alcora.dk'),
        Archive_folder_name=compose('Arkiv_10_2024_>'),
        OneDrive_folder=compose('/Fakturaer'),
        Mailbox_base=compose("@concat('https://graph.microsoft.com/v1.0/users/', encodeUriComponent(outputs('Shared_mailbox')), '/')"),
        **{ 'Initialize_' + name: {'type': 'InitializeVariable', 'inputs': {'variables': [
            {'name': name, 'type': typ, 'value': value}]}, 'runAfter': {}} for name, typ, value in [
                ('Start_utc', 'string', '@utcNow()'), ('Processed_messages', 'array', []),
                ('State_file_ID', 'string', ''), ('Message_page_URL', 'string', ''),
                ('Attachment_page_URL', 'string', ''), ('Existing_PDFs', 'array', []),
                ('Folders_to_scan', 'array', []), ('Folder_index', 'integer', 0),
                ('Duplicate_found', 'boolean', False), ('Fatal_error', 'boolean', False),
                ('Pending_mails', 'array', []),
            ]},
        Find_root_archive=http(folder_request('mailFolders')),
        Find_inbox_archive=http(folder_request('mailFolders/inbox/childFolders')),
        Archive_matches=compose("@union(body('Find_root_archive')?['value'], body('Find_inbox_archive')?['value'])"),
        Require_exact_archive=condition(
            "@or(not(equals(length(outputs('Archive_matches')), 1)), not(empty(body('Find_root_archive')?['@odata.nextLink'])), not(empty(body('Find_inbox_archive')?['@odata.nextLink'])))",
            {'Stop_ambiguous_archive': fail('ArchiveFolderNotUnique', 'Expected exactly one Arkiv_10_2024_> folder at mailbox root or directly under Inbox. Check its exact name/location.')}),
        Read_saved_state=drive('GetFileContentByPath', path="@concat(outputs('OneDrive_folder'), '/_automation-state.json')", inferContentType=False),
        Load_or_initialize_state=load_state,
        Monitored_folders=compose("@createArray('inbox', first(outputs('Archive_matches'))?['id'])"),
        For_each_mail_folder=foreach("@outputs('Monitored_folders')", sequence(
            Set_message_page_URL=variable('Message_page_URL',
                "@concat(outputs('Mailbox_base'), 'mailFolders/', encodeUriComponent(items('For_each_mail_folder')), '/messages?$filter=', encodeUriComponent(concat('receivedDateTime ge ', variables('Start_utc'), ' and hasAttachments eq true')), '&$orderby=receivedDateTime asc&$select=id,subject,receivedDateTime,hasAttachments&$top=100')"),
            Read_all_message_pages=until("@empty(variables('Message_page_URL'))", {
                'Validate_message_page_URL': checked_http('Get_message_page', "@variables('Message_page_URL')", {
                    'Collect_each_mail': foreach("@body('Get_message_page')?['value']", {
                        'Collect_unseen_mail': condition("@not(contains(variables('Processed_messages'), items('Collect_each_mail')?['id']))",
                            {'Queue_unseen_mail': append('Pending_mails', "@items('Collect_each_mail')")}),
                    }),
                    'Next_message_page': variable('Message_page_URL', "@coalesce(body('Get_message_page')?['@odata.nextLink'], '')"),
                }),
            }),
        )),
        Stop_failed_message_scan=condition("@equals(variables('Fatal_error'), true)",
            {'Message_scan_failed': fail('MessageScanFailed', 'Reading mailbox pages failed; no mail has been marked processed.')}),
        Index_files_for_new_mails=condition("@greater(length(variables('Pending_mails')), 0)", index_files),
        Stop_failed_file_scan=condition("@equals(variables('Fatal_error'), true)",
            {'File_scan_failed': fail('FileScanFailed', 'Reading the complete OneDrive PDF list failed; no PDF has been saved.')}),
        For_each_mail=foreach("@variables('Pending_mails')", process_mail),
        Stop_failed_attachment_scan=condition("@equals(variables('Fatal_error'), true)",
            {'Attachment_scan_failed': fail('AttachmentScanFailed', 'Reading or saving PDF attachments failed. The next run retries incomplete mail.')}),
    )
    # The expected first-run 404 is handled; authentication/permission failures
    # are fatal and must never reset the persisted future-only boundary.
    actions['Load_or_initialize_state']['runAfter'] = {'Read_saved_state': ['Succeeded', 'Failed', 'TimedOut']}
    return {
        '$schema': legacy['$schema'], 'contentVersion': '1.0.0.0',
        'parameters': copy.deepcopy(legacy['parameters']),
        'triggers': {'Every_15_minutes': {'type': 'Recurrence',
            'recurrence': {'frequency': 'Minute', 'interval': 15},
            'runtimeConfiguration': {'concurrency': {'runs': 1}}}},
        'actions': actions, 'outputs': {},
    }


def make_shared_package():
    documents = make_package()
    flow = documents[f'Microsoft.Flow/flows/{resource_id("flow")}/definition.json']
    flow['properties']['definition'] = definition()
    flow['properties']['description'] = (
        'Future invoice PDFs from faktura@alcora.dk Inbox and Arkiv_10_2024_>. '
        'One serial scan every 15 minutes; Copenhagen received-date folders; '
        'exact PDF content deduplication and persistent processed-mail state.')
    return documents
