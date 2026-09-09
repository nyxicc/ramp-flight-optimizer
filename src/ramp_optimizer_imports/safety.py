"""Bounded upload consumption and pre-open XLSX container validation."""

from dataclasses import dataclass, fields
from hashlib import sha256
from io import BytesIO
import os
from pathlib import PurePosixPath
import re
import unicodedata
from xml.etree import ElementTree
from zipfile import ZipFile

from ramp_optimizer_imports.models import ImportError


@dataclass(frozen=True, slots=True)
class UploadLimits:
    max_bytes: int = 10 * 1024 * 1024
    max_members: int = 256
    max_uncompressed_bytes: int = 64 * 1024 * 1024
    max_member_bytes: int = 16 * 1024 * 1024
    max_compression_ratio: int = 100
    max_rows: int = 5000
    max_columns: int = 64
    max_cells: int = 100000

    def __post_init__(self):
        if any(type(getattr(self, f.name)) is not int or getattr(self, f.name) < 1 for f in fields(self)):
            raise ValueError("Import limits must be positive integers.")

    @classmethod
    def from_environment(cls):
        return cls(**{f.name: int(os.environ['RAMP_IMPORT_' + f.name.upper()])
                      for f in fields(cls) if 'RAMP_IMPORT_' + f.name.upper() in os.environ})


MEDIA_TYPES = frozenset({
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/octet-stream", "application/zip", "application/x-zip-compressed",
})


def validate_filename(filename: str | None, media_type: str | None) -> tuple[str, str]:
    if not filename:
        raise ImportError("UPLOAD_FILENAME_REQUIRED")
    # Never retain a client path. Both Windows and POSIX separators are display-only.
    safe = filename.replace('\\', '/').rsplit('/', 1)[-1]
    safe = ''.join('_' if unicodedata.category(c).startswith('C') or c == ':' else c for c in safe)
    if not safe.lower().endswith('.xlsx') or len(safe) > 255:
        raise ImportError("UNSUPPORTED_FILE_TYPE", 415)
    media = (media_type or 'application/octet-stream').split(';', 1)[0].strip().lower()
    if media not in MEDIA_TYPES:
        raise ImportError("UNSUPPORTED_FILE_TYPE", 415)
    return safe, media


async def read_upload(upload, limits: UploadLimits) -> tuple[bytes, str]:
    """Stop at limit + 1; callers also bound the outer multipart request."""
    content = bytearray()
    digest = sha256()
    try:
        while True:
            chunk = await upload.read(min(64 * 1024, limits.max_bytes + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > limits.max_bytes:
                raise ImportError("UPLOAD_TOO_LARGE", 413)
            digest.update(chunk)
        if not content:
            raise ImportError("UPLOAD_EMPTY")
        return bytes(content), digest.hexdigest()
    finally:
        await upload.close()


def validate_container(content: bytes, limits: UploadLimits) -> None:
    if not content:
        raise ImportError("UPLOAD_EMPTY")
    if len(content) > limits.max_bytes:
        raise ImportError("UPLOAD_TOO_LARGE", 413)
    if not content.startswith(b'PK\x03\x04'):
        raise ImportError("INVALID_XLSX_CONTAINER")
    try:
        with ZipFile(BytesIO(content)) as archive:
            members = archive.infolist()
            names = {member.filename for member in members}
            if (len(members) > limits.max_members or len(names) != len(members)
                    or sum(m.file_size for m in members) > limits.max_uncompressed_bytes):
                raise ImportError("SUSPICIOUS_XLSX_ARCHIVE")
            if not {'[Content_Types].xml', 'xl/workbook.xml', '_rels/.rels'} <= names:
                raise ImportError("INVALID_XLSX_CONTAINER")
            for member in members:
                name = member.filename
                if (member.flag_bits & 1 or member.file_size > limits.max_member_bytes
                        or member.file_size / max(1, member.compress_size) > limits.max_compression_ratio
                        or name.startswith('/') or '\\' in name or ':' in name
                        or '..' in PurePosixPath(name).parts):
                    raise ImportError("SUSPICIOUS_XLSX_ARCHIVE")
                if name.lower().endswith('vbaproject.bin'):
                    raise ImportError("UNSUPPORTED_FILE_TYPE", 415)
                # Reading bounded members verifies CRC and actual decompression. No extraction.
                with archive.open(member) as stream:
                    data = stream.read(limits.max_member_bytes + 1)
                if len(data) != member.file_size or len(data) > limits.max_member_bytes:
                    raise ImportError("SUSPICIOUS_XLSX_ARCHIVE")
                # Relationship targets can rename XML parts. Inspect content, not just
                # paths/extensions, including UTF-16/32 declarations and BOMs.
                folded = data.replace(b'\x00', b'').upper()
                if b'<!DOCTYPE' in folded or b'<!ENTITY' in folded:
                    raise ImportError("SUSPICIOUS_XLSX_ARCHIVE")
                if name == '[Content_Types].xml' and b'MACROENABLED' in folded:
                    raise ImportError("UNSUPPORTED_FILE_TYPE", 415)
                looks_xml = folded.lstrip(b'\xef\xbb\xbf\xff\xfe \t\r\n').startswith(b'<')
                if looks_xml or name.endswith(('.xml', '.rels')):
                    _validate_sheet(data, limits)
    except ImportError:
        raise
    except Exception:
        raise ImportError("INVALID_XLSX_CONTAINER") from None


def _validate_sheet(data: bytes, limits: UploadLimits) -> None:
    cells = rows = row_cells = 0
    for _, element in ElementTree.iterparse(BytesIO(data), events=('start',)):
        tag = element.tag.rsplit('}', 1)[-1]
        if tag == 'row':
            row_cells = 0
            rows += 1
            if rows > limits.max_rows or int(element.get('r', rows)) > limits.max_rows:
                raise ImportError("SUSPICIOUS_XLSX_ARCHIVE")
        if tag == 'c':
            cells += 1
            row_cells += 1
            if cells > limits.max_cells or row_cells > limits.max_columns:
                raise ImportError("SUSPICIOUS_XLSX_ARCHIVE")
        if tag in {'dimension', 'c'}:
            ref = element.get('ref' if tag == 'dimension' else 'r', '')
            for column, row in re.findall(r'([A-Z]+)([0-9]+)', ref.upper()):
                number = 0
                for letter in column:
                    number = number * 26 + ord(letter) - 64
                if number > limits.max_columns or int(row) > limits.max_rows:
                    raise ImportError("SUSPICIOUS_XLSX_ARCHIVE")
