"""Review lifecycle, HTTP, privacy, safety, and immutable storage acceptance tests."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, time, timezone
from hashlib import sha256
from io import BytesIO
import json
import importlib
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from ramp_optimizer import Employee, OptimizerConfig, Qualification, OperationalRole
from ramp_optimizer.models import ScheduleImportResult
from ramp_optimizer.teamwork_import import import_teamwork_schedule
from ramp_optimizer_api.app import create_app
from ramp_optimizer_api.import_schemas import ImportMetadataRequest
from ramp_optimizer_api.mapping import employee_to_domain
from ramp_optimizer_api.services import PersistenceService
from ramp_optimizer_imports.employee_schedule import TeamWorkAdapter
from ramp_optimizer_imports.enums import ImportStatus
from ramp_optimizer_imports.models import ImportError, RowCorrection
from ramp_optimizer_imports.safety import UploadLimits, read_upload, validate_container
from ramp_optimizer_imports.serialization import canonical_json, load_preview
from ramp_optimizer_imports.services import ImportService
from ramp_optimizer_persistence.database import create_database_engine, make_session_factory
from ramp_optimizer_persistence.errors import PersistenceIntegrityError, ResourceNotFoundError
from ramp_optimizer_persistence.imports import SQLImportRepository, import_transactions
from ramp_optimizer_persistence.models import Base, ImportJobRow, ImportRevisionRow, OperationalDayRow
from tests.import_fixtures import DAY, HEADERS, ROSTER, SECRET, row, workbook

UPLOAD = '/api/v1/imports/teamwork-employee-schedule'


@pytest.fixture
def storage(tmp_path):
    engine = create_database_engine(f'sqlite:///{(tmp_path / "imports.sqlite").as_posix()}')
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    yield engine, sessions
    engine.dispose()


@pytest.fixture
def service(storage):
    return ImportService(import_transactions(storage[1]))


@pytest.fixture
def client(storage):
    def forbidden(*args, **kwargs):
        raise AssertionError('Import tests must never invoke optimization')
    persistence = PersistenceService(storage[1], api_version='1', optimizer=forbidden)
    with TestClient(create_app(persistence_service=persistence), raise_server_exceptions=False) as client:
        yield client


def upload(client, content=None, *, filename='fictional.xlsx', media='application/octet-stream', metadata=None):
    return client.post(UPLOAD, files={'workbook': (filename, workbook() if content is None else content, media)},
                       data={'metadata': json.dumps(metadata or {'operational_date': DAY.isoformat(), 'roster': ROSTER})})


def create(service, content=None):
    roster = tuple(employee_to_domain(e) for e in ImportMetadataRequest(operational_date=DAY, roster=ROSTER).roster)
    return service.upload(workbook() if content is None else content, 'fictional.xlsx', 'application/octet-stream', DAY, roster, OptimizerConfig())


def correct(client, record, **changes):
    return client.post(f"/api/v1/imports/{record['import_id']}/corrections", json={
        'revision': record['preview']['revision'],
        'corrections': [{'row_id': record['preview']['rows'][0]['row_id'], **changes}],
    })


def confirm(client, record, revision=None):
    return client.post(f"/api/v1/imports/{record['import_id']}/confirm", json={
        'revision': record['preview']['revision'] if revision is None else revision,
    })


def codes(record):
    return {i['code'] for i in record['preview']['issues']}


def test_upload_preview_hash_order_and_no_implicit_snapshot(client, storage):
    content = workbook(titles=True, headers=[' date ', 'POSITION', 'employee', *HEADERS[3:]])
    response = upload(client, content)
    assert response.status_code == 201, response.text
    record = response.json()
    assert record['sha256'] == sha256(content).hexdigest()
    assert record['byte_size'] == len(content)
    assert record['status'] == 'READY_TO_CONFIRM'
    assert record['preview']['confirmation_eligible']
    assert record['preview']['rows'][0]['source_row'] == 4
    assert record['preview']['employees'][0]['enabled'] is False
    assert record['preview']['employees'][0]['qualifications'] == ['PUSH']
    for suffix in ('', '/preview'):
        assert client.get(f"/api/v1/imports/{record['import_id']}{suffix}").json() == record
    with storage[1]() as session:
        assert session.scalar(select(func.count()).select_from(OperationalDayRow)) == 0
        assert session.scalar(select(func.count()).select_from(ImportRevisionRow)) == 1


@pytest.mark.parametrize(('content','filename','media','status','code'), [
    (b'', 'fictional.xlsx', 'application/octet-stream', 422, 'UPLOAD_EMPTY'),
    (b'legacy', 'fictional.xls', 'application/octet-stream', 415, 'UNSUPPORTED_FILE_TYPE'),
    (b'%PDF', 'fictional.pdf', 'application/pdf', 415, 'UNSUPPORTED_FILE_TYPE'),
    (b'a,b', 'fictional.csv', 'text/csv', 415, 'UNSUPPORTED_FILE_TYPE'),
    (b'arbitrary', 'fictional.xlsx', 'application/octet-stream', 422, 'INVALID_XLSX_CONTAINER'),
    (b'PK\x03\x04malformed', 'fictional.xlsx', 'application/octet-stream', 422, 'INVALID_XLSX_CONTAINER'),
    (b'x', 'fictional.xlsx', 'application/pdf', 415, 'UNSUPPORTED_FILE_TYPE'),
    (b'\xd0\xcf\x11\xe0encrypted', 'fictional.xlsx', 'application/octet-stream', 422, 'INVALID_XLSX_CONTAINER'),
])
def test_file_envelope_errors(client, content, filename, media, status, code):
    response = upload(client, content, filename=filename, media=media)
    assert response.status_code == status
    assert response.json()['error']['code'] == code
    assert 'Traceback' not in response.text and 'C:\\' not in response.text


@pytest.mark.parametrize('media', ['application/octet-stream', 'application/zip', 'application/x-zip-compressed',
                                  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'])
def test_documented_media_variations(client, media):
    assert upload(client, media=media).status_code == 201


@pytest.mark.parametrize(('content','code'), [
    (workbook(sheet='Other'), 'MISSING_SCHEDULE_WORKSHEET'),
    (workbook(headers=['Date','Employee']), 'MISSING_REQUIRED_HEADERS'),
])
def test_structural_rejections_persist_and_block_confirmation(client, content, code):
    response = upload(client, content)
    assert response.status_code == 201
    record = response.json()
    assert record['status'] == 'REJECTED' and code in codes(record)
    assert confirm(client, record).status_code == 409
    assert client.get(f"/api/v1/imports/{record['import_id']}/preview").json() == record


@pytest.mark.parametrize(('name','code'), [('Fictional Unknown', 'UNMATCHED_EMPLOYEE'), ('Fictional Twin', 'AMBIGUOUS_EMPLOYEE')])
def test_unresolved_matches_can_be_reviewed(client, name, code):
    record = upload(client, workbook([row(Employee=name)])).json()
    assert code in codes(record) and record['unresolved_row_count'] == 1
    assert not record['preview']['confirmation_eligible']
    assert confirm(client, record).status_code == 409
    result = correct(client, record, employee_id='SYN002', enabled=True, qualifications=['CLOSE_OUT'])
    assert result.status_code == 200, result.text
    revised = result.json()
    assert revised['preview']['confirmation_eligible']
    assert revised['preview']['revision'] == 2
    assert revised['preview']['rows'][0]['employee_id'] == 'SYN002'
    assert code not in codes(revised)
    assert any(i['code'] == code for i in revised['preview']['source_issues'])
    assert confirm(client, revised).status_code == 200


def test_revision_conflicts_terminal_confirmation_and_snapshot(client, storage, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Optimization must not run')
    monkeypatch.setattr(importlib.import_module('ramp_optimizer_api.app'), 'optimize_flight_assignments', forbidden)
    record = upload(client).json()
    revised = correct(client, record, start='2035-04-15T06:00:00', normalized_role='TRAINEE', enabled=True, qualifications=[]).json()
    assert revised['preview']['rows'][0]['row_id'] == record['preview']['rows'][0]['row_id']
    stale = correct(client, record, excluded=True)
    assert stale.status_code == 409 and stale.json()['error']['code'] == 'IMPORT_REVISION_CONFLICT'
    assert confirm(client, revised, 1).json()['error']['code'] == 'IMPORT_REVISION_CONFLICT'
    confirmed = confirm(client, revised)
    assert confirmed.status_code == 200, confirmed.text
    confirmed = confirmed.json()
    assert confirmed['status'] == 'CONFIRMED'
    assert confirm(client, confirmed).json() == confirmed
    assert correct(client, confirmed, excluded=True).json()['error']['code'] == 'IMPORT_ALREADY_CONFIRMED'
    day_id = confirmed['confirmed_operational_day_id']
    day = client.get(f'/api/v1/operational-days/{day_id}').json()
    assert day['input']['operational_day']['operational_date'] == DAY.isoformat()
    assert day['input']['operational_day']['flights'] == []
    assert day['input']['operational_day']['fixed_assignments'] == []
    assert day['input']['operational_day']['employees'][0]['qualifications'] == []
    assert day['input']['operational_day']['employee_shifts'][0]['normalized_role'] == 'TRAINEE'
    assert day['optimization_eligible'] is False and day['optimization_blockers'] == ['FLIGHT_DATA_REQUIRED']
    assert not confirmed['preview']['optimization_eligible']
    assert client.post(f'/api/v1/operational-days/{day_id}/optimizations').json()['error']['code'] == 'FLIGHT_DATA_REQUIRED'
    with storage[1]() as session:
        assert session.scalar(select(func.count()).select_from(OperationalDayRow)) == 1
        assert session.scalar(select(func.count()).select_from(ImportRevisionRow)) == 2
        first = load_preview(session.get(ImportRevisionRow, (record['import_id'], 1)).preview_json)
        assert first.rows[0].values.start.hour == 5


@pytest.mark.parametrize('changes', [
    {'employee_id':'not-in-roster'}, {'qualifications':['FLY']}, {'normalized_role':'PILOT'},
    {'qualifications':['PUSH','PUSH']}, {'enabled':'yes'}, {'excluded':None},
    {'row_id':str(uuid4()), 'excluded':True}, {'vacancy':True,'employee_id':'SYN001'},
])
def test_invalid_corrections_rejected_without_new_revision(client, changes):
    record = upload(client).json()
    result = correct(client, record, **changes)
    assert result.status_code == 422, result.text
    assert client.get(f"/api/v1/imports/{record['import_id']}").json() == record


@pytest.mark.parametrize(('changes','code'), [
    ({'start':'2035-04-15T14:00:00'}, 'INVALID_SHIFT_RANGE'),
    ({'end':'2035-04-16T05:00:00'}, 'IMPLAUSIBLY_LONG_SHIFT'),
    ({'start':'2035-04-14T05:00:00'}, 'INVALID_DATE'),
    ({'end':'2035-04-15T13:00:01'}, 'INVALID_EMPLOYEE_SHIFT_MINUTES'),
    ({'start':'2035-04-15T05:00:00+00:00'}, 'MIXED_DATETIME_AWARENESS'),
])
def test_corrected_values_are_revalidated_and_block_confirm(client, changes, code):
    record = upload(client).json()
    response = correct(client, record, **changes)
    assert response.status_code == 200, response.text
    revised = response.json()
    assert code in codes(revised) and not revised['preview']['confirmation_eligible']
    assert confirm(client, revised).status_code == 409


def test_vacancy_conversion_restoration_and_exclusion(client):
    record = upload(client, workbook([row(Employee=None)])).json()
    assert record['vacancy_count'] == 1 and record['accepted_shift_count'] == 0
    restored = correct(client, record, vacancy=False, employee_id='SYN001').json()
    assert restored['vacancy_count'] == 0 and restored['accepted_shift_count'] == 1
    vacant = correct(client, restored, vacancy=True).json()
    assert vacant['preview']['rows'][0]['employee_id'] is None
    unresolved = correct(client, vacant, vacancy=False).json()
    assert not unresolved['preview']['confirmation_eligible']
    excluded = correct(client, unresolved, excluded=True).json()
    assert excluded['accepted_shift_count'] == 0 and excluded['preview']['confirmation_eligible']
    result = confirm(client, excluded).json()
    day = client.get('/api/v1/operational-days/' + result['confirmed_operational_day_id']).json()
    assert day['input']['operational_day']['employee_shifts'] == []


def test_duplicates_overlaps_and_review_warning(client):
    record = upload(client, workbook([row(), row(), row(Start=time(7), End=time(9))])).json()
    assert 'DUPLICATE_SCHEDULE_ROW' in codes(record)
    assert record['preview']['rows'][1]['excluded']
    assert any('OVERLAPPING' in c for c in codes(record))
    response = client.post(f"/api/v1/imports/{record['import_id']}/corrections", json={
        'revision':1, 'corrections': [{'row_id':record['preview']['rows'][2]['row_id'], 'excluded':True}],
    })
    revised = response.json()
    assert revised['preview']['confirmation_eligible']
    restored = client.post(f"/api/v1/imports/{record['import_id']}/corrections", json={
        'revision':2, 'corrections':[{'row_id':record['preview']['rows'][1]['row_id'], 'excluded':False}],
    }).json()
    assert any('DUPLICATE' in c for c in codes(restored))
    assert not restored['preview']['confirmation_eligible']


@pytest.mark.parametrize('field', ['Date','Start','End','Employee','Position'])
def test_formulas_in_required_fields_need_reviewed_replacements(client, field):
    record = upload(client, workbook([row(**{field:'=1+1'})])).json()
    assert 'FORMULA_VALUE_UNAVAILABLE' in codes(record)
    assert not record['preview']['confirmation_eligible']
    assert '=1+1' not in json.dumps(record)
    revised = correct(client, record, employee_id='SYN001', normalized_role='RAMP_AGENT',
                      start='2035-04-15T05:00:00', end='2035-04-15T13:00:00').json()
    assert revised['preview']['confirmation_eligible']


def test_ignored_formulas_and_warnings_do_not_execute_or_grant_qualifications(client):
    record = upload(client, workbook([row(Notes='=HYPERLINK("https://invalid.example")', Hours=7, Position='Unmapped')])).json()
    assert {'UNKNOWN_POSITION','HOURS_DURATION_MISMATCH'} <= codes(record)
    assert record['preview']['confirmation_eligible'] and record['preview']['rows'][0]['notes_present']
    assert 'HYPERLINK' not in json.dumps(record)
    assert record['preview']['employees'][0]['qualifications'] == ['PUSH']


@pytest.mark.parametrize(('values','eligible'), [
    ({'Date':'2035-04-15','Start':'05:00','End':'13:00'}, True),
    ({'Start':time(22),'End':time(6)}, True),
    ({'Start':time(5),'End':time(5)}, False),
    ({'Start':time(1),'End':time(23)}, False),
])
def test_text_native_overnight_zero_and_long_shifts(client, values, eligible):
    record = upload(client, workbook([row(**values)])).json()
    assert record['preview']['confirmation_eligible'] is eligible


def test_notes_names_and_raw_bytes_absent_from_storage_responses_and_logs(client, storage, caplog):
    content = workbook([row(), row(Employee='Distinctive Fictional Unmatched', Start=time(14), End=time(18))])
    core = import_teamwork_schedule(BytesIO(content), (Employee('SYN001','Fictional Avery'),))
    assert SECRET not in repr(core)
    record = upload(client, content, filename='../../inert/fictional.xlsx').json()
    assert record['original_filename'] == 'fictional.xlsx'
    assert SECRET not in json.dumps(record)
    assert 'Distinctive Fictional Unmatched' not in json.dumps(record)
    assert SECRET not in caplog.text
    assert SECRET not in confirm(client, record).text
    with storage[1]() as session:
        for table in ('import_jobs', 'import_revisions', 'operational_days'):
            values = session.execute(text(f'SELECT * FROM {table}')).all()
            assert SECRET not in repr(values)
            assert 'Distinctive Fictional Unmatched' not in repr(values)
            assert not any(isinstance(v, bytes) for value in values for v in value)
        payload = session.scalar(select(ImportRevisionRow.preview_json))
        assert 'notes_present' in payload and 'PK' not in payload


def test_unknown_and_malformed_ids(client):
    for suffix in ('', '/preview'):
        assert client.get('/api/v1/imports/not-uuid'+suffix).status_code == 422
        assert client.get('/api/v1/imports/'+str(uuid4())+suffix).status_code == 404
    assert client.post('/api/v1/imports/'+str(uuid4())+'/confirm', json={'revision':1}).status_code == 404
    assert client.post('/api/v1/imports/not-uuid/confirm', json={'revision':1}).status_code == 422


def test_duplicate_uploads_are_separate_and_canonical(service, storage):
    content = workbook()
    first, second = create(service, content), create(service, content)
    assert first.import_id != second.import_id and first.sha256 == second.sha256
    assert load_preview(canonical_json(first.preview)) == first.preview
    assert service.get(first.import_id) == first
    a, b = json.loads(canonical_json(first.preview)), json.loads(canonical_json(second.preview))
    for value in (a, b):
        for r in value['rows']:
            r.pop('row_id')
    assert a == b


@pytest.mark.parametrize('operation', ['create', 'revise', 'confirm'])
def test_transaction_rollbacks(service, storage, monkeypatch, operation):
    current = create(service) if operation != 'create' else None
    original = getattr(SQLImportRepository, operation)
    def fail(self, record):
        original(self, record)
        self.session.flush()
        raise RuntimeError('FICTIONAL failure after staged writes')
    monkeypatch.setattr(SQLImportRepository, operation, fail)
    with pytest.raises(RuntimeError):
        if operation == 'create':
            create(service)
        elif operation == 'revise':
            service.correct(current.import_id, 1, (RowCorrection(current.preview.rows[0].row_id, excluded=True),))
        else:
            service.confirm(current.import_id, 1)
    with storage[1]() as session:
        assert session.scalar(select(func.count()).select_from(OperationalDayRow)) == 0
        assert session.scalar(select(func.count()).select_from(ImportRevisionRow)) == (0 if current is None else 1)
    if current:
        assert service.get(current.import_id) == current


def test_concurrent_confirmations_create_one_snapshot(service, storage):
    current = create(service)
    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(lambda _: service.confirm(current.import_id, 1), range(2)))
    assert records[0] == records[1]
    with storage[1]() as session:
        assert session.scalar(select(func.count()).select_from(OperationalDayRow)) == 1


def test_concurrent_corrections_have_one_winner(service):
    current = create(service)
    def attempt(_):
        try:
            return service.correct(current.import_id, 1, (RowCorrection(current.preview.rows[0].row_id, excluded=True),)).preview.revision
        except ImportError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, range(2)))
    assert sorted(outcomes, key=str) == [2, 'IMPORT_REVISION_CONFLICT']


def test_tamper_detection_and_revision_uniqueness(service, storage):
    current = create(service)
    with pytest.raises(IntegrityError):
        with storage[1].begin() as session:
            session.add(ImportRevisionRow(import_id=current.import_id, revision=1, created_at='x', preview_json='{}', preview_hash='x'))
    with storage[1].begin() as session:
        session.get(ImportRevisionRow, (current.import_id,1)).preview_json = '{}'
    with pytest.raises(PersistenceIntegrityError):
        service.get(current.import_id)


def test_upload_reads_stop_at_limit_and_close():
    class Upload:
        read_bytes = 0
        closed = False
        async def read(self, size):
            assert size > 0
            self.read_bytes += size
            return b'x' * size
        async def close(self):
            self.closed = True
    value = Upload()
    with pytest.raises(ImportError, match='UPLOAD_TOO_LARGE'):
        asyncio.run(read_upload(value, UploadLimits(max_bytes=100)))
    assert value.read_bytes == 101 and value.closed


def test_api_size_limit_and_outer_body_limit(storage):
    with TestClient(create_app(session_factory=storage[1], import_limits=UploadLimits(max_bytes=100))) as client:
        assert upload(client, b'x' * 101).status_code == 413
        assert upload(client, b'x' * (300 * 1024)).status_code == 413


def test_outer_body_stops_before_remaining_payload():
    from ramp_optimizer_api.import_routes import BoundedImportBody
    messages = []
    calls = 0
    async def receive():
        nonlocal calls
        calls += 1
        return {'type':'http.request', 'body':b'x' * (128 * 1024), 'more_body':True}
    async def send(message):
        messages.append(message)
    async def forbidden(*args):
        raise AssertionError('oversized body reached parser')
    asyncio.run(BoundedImportBody(forbidden, 100)({'type':'http', 'method':'POST', 'path':UPLOAD}, receive, send))
    assert calls == 3 and messages[0]['status'] == 413


@pytest.mark.parametrize('limits', [
    UploadLimits(max_members=1), UploadLimits(max_member_bytes=100),
    UploadLimits(max_uncompressed_bytes=100), UploadLimits(max_compression_ratio=1),
    UploadLimits(max_rows=1), UploadLimits(max_columns=2), UploadLimits(max_cells=1),
])
def test_archive_limits(limits):
    with pytest.raises(ImportError, match='SUSPICIOUS_XLSX_ARCHIVE'):
        validate_container(workbook(), limits)


@pytest.mark.parametrize(('member','data','code'), [
    ('../escape.xml', b'<x/>', 'SUSPICIOUS_XLSX_ARCHIVE'),
    ('xl/vbaProject.bin', b'macro', 'UNSUPPORTED_FILE_TYPE'),
    ('extra.xml', b'<!DOCTYPE x [<!ENTITY y "secret">]><x>&y;</x>', 'SUSPICIOUS_XLSX_ARCHIVE'),
    ('xl/worksheets/sheet1.xml', b'<worksheet><dimension ref="A1:XFD1048576"/></worksheet>', 'SUSPICIOUS_XLSX_ARCHIVE'),
    ('xl/worksheets/sheet1.xml', b'<worksheet><dimension ref="A1:A1"/><row r="900000"/></worksheet>', 'SUSPICIOUS_XLSX_ARCHIVE'),
    ('xl/custom.bin', b'<worksheet><dimension ref="A1:XFD1048576"/></worksheet>', 'SUSPICIOUS_XLSX_ARCHIVE'),
    ('xl/custom.bin', b'<!DOCTYPE x [<!ENTITY y "secret">]><x>&y;</x>', 'SUSPICIOUS_XLSX_ARCHIVE'),
    ('xl/workbook.xml', b'<broken', 'INVALID_XLSX_CONTAINER'),
])
def test_suspicious_zip_members(member, data, code):
    source = ZipFile(BytesIO(workbook()))
    buffer = BytesIO()
    with ZipFile(buffer, 'w', ZIP_DEFLATED) as dest:
        for item in source.infolist():
            if item.filename != member:
                dest.writestr(item.filename, source.read(item.filename))
        dest.writestr(member, data)
    source.close()
    with pytest.raises(ImportError, match=code):
        validate_container(buffer.getvalue(), UploadLimits())


def test_preview_retrieval_and_corrections_do_not_reparse(service, monkeypatch):
    calls = 0
    original = TeamWorkAdapter.parse
    def counted(self, *args):
        nonlocal calls
        calls += 1
        return original(self, *args)
    monkeypatch.setattr(TeamWorkAdapter, 'parse', counted)
    current = create(service)
    service.get(current.import_id)
    service.correct(current.import_id, 1, (RowCorrection(current.preview.rows[0].row_id, excluded=True),))
    service.confirm(current.import_id, 2)
    assert calls == 1


def test_domain_dependency_boundaries():
    root = Path(__file__).parents[1] / 'src'
    for file in (root / 'ramp_optimizer').glob('*.py'):
        source = file.read_text()
        assert 'from fastapi' not in source and 'import fastapi' not in source
        assert 'from sqlalchemy' not in source and 'import sqlalchemy' not in source
        assert 'ramp_optimizer_api' not in source and 'ramp_optimizer_persistence' not in source

    for file in (root / 'ramp_optimizer_imports').glob('*.py'):
        source = file.read_text()
        assert 'optimize_flight_assignments' not in source
        assert 'from fastapi' not in source and 'from sqlalchemy' not in source
        assert 'ramp_optimizer_api' not in source and 'ramp_optimizer_persistence' not in source


def test_missing_position_blocks_until_corrected(client):
    record = upload(client, workbook([row(Position=None)])).json()
    assert 'MISSING_POSITION' in codes(record)
    assert not record['preview']['confirmation_eligible']
    revised = correct(client, record, normalized_role='RAMP_AGENT').json()
    assert revised['preview']['confirmation_eligible']


@pytest.mark.parametrize('metadata', [
    {'operational_date':'bad-date','roster':ROSTER},
    {'operational_date':DAY.isoformat(),'roster':[{'employee_id':'X','name':'Fictional','qualifications':['FLY']}]},
    {'operational_date':DAY.isoformat(),'roster':ROSTER + [ROSTER[0]]},
    {'operational_date':DAY.isoformat(),'roster':[{'employee_id':'','name':'Fictional'}]},
    {'operational_date':DAY.isoformat(),'roster':ROSTER,'config':{'minimum_staff':0}},
])
def test_authoritative_roster_and_metadata_validation(client, metadata):
    response = upload(client, metadata=metadata)
    assert response.status_code == 422
    assert SECRET not in response.text


def test_invalid_metadata_json_and_missing_filename(client):
    response = client.post(UPLOAD, files={'workbook':('fictional.xlsx',workbook())}, data={'metadata':'{bad-json'})
    assert response.status_code == 422 and response.json()['error']['code'] == 'REQUEST_VALIDATION_ERROR'
    response = client.post(UPLOAD, files={'workbook':('',workbook())}, data={'metadata':'{}'})
    assert response.status_code == 422


def test_multiple_bad_correction_targets_report_all(client):
    record = upload(client).json()
    response = client.post(f"/api/v1/imports/{record['import_id']}/corrections", json={
        'revision':1, 'corrections':[
            {'row_id':str(uuid4()),'excluded':True},
            {'row_id':record['preview']['rows'][0]['row_id'],'employee_id':'unknown'},
        ],
    })
    assert response.status_code == 422
    assert {d['code'] for d in response.json()['error']['details']} == {'UNKNOWN_IMPORT_ROW','UNKNOWN_SHIFT_EMPLOYEE'}


def test_conflicting_day_employee_overrides_rejected(client):
    record = upload(client, workbook([row(),row(Start=time(14),End=time(18))])).json()
    response = client.post(f"/api/v1/imports/{record['import_id']}/corrections", json={
        'revision':1, 'corrections':[
            {'row_id':record['preview']['rows'][0]['row_id'],'enabled':True},
            {'row_id':record['preview']['rows'][1]['row_id'],'enabled':False},
        ],
    })
    assert response.status_code == 422
    assert response.json()['error']['details'][0]['code'] == 'CONFLICTING_EMPLOYEE_OVERRIDE'


@pytest.mark.parametrize('filename', ['../../fictional.xlsx', 'C:\\private\\fictional.xlsx', 'odd\x01name.xlsx'])
def test_filenames_are_inert_display_values(service, filename):
    current = service.upload(workbook(), filename, 'application/octet-stream', DAY, (), OptimizerConfig())
    assert '/' not in current.original_filename and '\\' not in current.original_filename
    assert '\x01' not in current.original_filename and ':' not in current.original_filename


@pytest.mark.parametrize('invalid', [False, True])
def test_upload_temporary_resources_close(client, monkeypatch, invalid):
    from starlette.datastructures import UploadFile
    closed = []
    original = UploadFile.close
    async def tracked(self):
        await original(self)
        closed.append(self.file.closed)
    monkeypatch.setattr(UploadFile, 'close', tracked)
    response = upload(client, b'bad' if invalid else workbook())
    assert response.status_code == (422 if invalid else 201)
    assert closed and all(closed)


def test_same_bytes_different_roster_have_independent_outcomes(service):
    content = workbook()
    matched = create(service, content)
    unmatched = service.upload(content, 'fictional.xlsx', 'application/octet-stream', DAY, (), OptimizerConfig())
    assert matched.sha256 == unmatched.sha256 and matched.import_id != unmatched.import_id
    assert matched.preview.confirmation_eligible and not unmatched.preview.confirmation_eligible


def test_multi_date_workbook_requires_review(client):
    record = upload(client, workbook([row(),row(Date='2035-04-16', Employee='Fictional Rowan')])).json()
    assert record['preview']['detected_operational_dates'] == ['2035-04-15','2035-04-16']
    assert 'INVALID_DATE' in codes(record) and not record['preview']['confirmation_eligible']


def test_domain_validation_is_used_at_confirmation(service, monkeypatch):
    from ramp_optimizer.validation import InputValidationError, ValidationIssue
    current = create(service)
    def reject(*args):
        raise InputValidationError((ValidationIssue('TEST_DOMAIN_REJECTION','employee_shifts','Fictional test rejection.'),))
    monkeypatch.setattr('ramp_optimizer_imports.services.validate_or_raise', reject)
    with pytest.raises(InputValidationError):
        service.confirm(current.import_id, 1)
    assert service.get(current.import_id).status == ImportStatus.READY_TO_CONFIRM


def test_hours_warnings_recalculate_after_time_review(client):
    record = upload(client, workbook([row(Hours=7)])).json()
    assert 'HOURS_DURATION_MISMATCH' in codes(record)
    revised = correct(client, record, end='2035-04-15T12:00:00').json()
    assert 'HOURS_DURATION_MISMATCH' not in codes(revised)
    assert any(i['code'] == 'HOURS_DURATION_MISMATCH' for i in revised['preview']['source_issues'])


def test_restoring_vacancy_requires_explicit_classification(client):
    record = upload(client, workbook([row(Employee=None)])).json()
    response = correct(client, record, employee_id='SYN001')
    assert response.status_code == 422
    assert response.json()['error']['details'][0]['code'] == 'VACANCY_RESTORATION_REQUIRED'


@pytest.mark.parametrize('end', [time(5), time(0)])
def test_invalid_range_can_be_fixed_by_end_only(client, end):
    record = upload(client, workbook([row(End=end)])).json()
    assert not record['preview']['confirmation_eligible']
    assert record['preview']['rows'][0]['start'] == '2035-04-15T05:00:00'
    revised = correct(client, record, end='2035-04-15T13:00:00').json()
    assert revised['preview']['confirmation_eligible']
