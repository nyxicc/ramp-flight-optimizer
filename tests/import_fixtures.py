"""Generated fictional workbooks. No operational workbook binaries are committed."""

from datetime import date, time
from io import BytesIO

from openpyxl import Workbook

DAY = date(2035, 4, 15)
SECRET = 'FICTIONAL-NOTE-ORCHID-729-DO-NOT-RETAIN'
ROSTER = [
    {'employee_id': 'SYN001', 'name': 'Fictional Avery', 'qualifications': ['PUSH'], 'enabled': False},
    {'employee_id': 'SYN002', 'name': 'Fictional Rowan', 'qualifications': []},
    {'employee_id': 'SYN003', 'name': 'Fictional Twin'},
    {'employee_id': 'SYN004', 'name': ' Fictional   Twin '},
]
HEADERS = ['Date', 'Position', 'Employee', 'Start', 'End', 'Notes', 'Hours', 'SwapBoard', 'Break']


def row(**values):
    defaults = dict(zip(HEADERS, [DAY, 'Ramp Agent', 'Fictional Avery', time(5), time(13), SECRET, 8, 'Yes', None]))
    defaults.update(values)
    return [defaults[h] for h in HEADERS]


def workbook(rows=None, *, headers=None, sheet='Schedule', titles=False):
    book = Workbook()
    ws = book.active
    ws.title = sheet
    if titles:
        ws.append(['Entirely fictional schedule for tests'])
        ws.append([])
    ws.append(HEADERS if headers is None else headers)
    for values in [row()] if rows is None else rows:
        ws.append(values)
    buffer = BytesIO()
    book.save(buffer)
    book.close()
    return buffer.getvalue()
