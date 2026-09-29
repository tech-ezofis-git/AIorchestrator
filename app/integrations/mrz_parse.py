"""Machine Readable Zone (MRZ) detection and parsing from OCR text (ICAO 9303).

Finds MRZ-like lines in extracted text, repairs common OCR confusions, identifies the format and
parses the fields, verifying every check digit:

- TD1: 3 lines x 30 (ID cards)
- TD2: 2 lines x 36 (ID cards / older passports); MRV-B: 2 x 36 starting with "V" (visa)
- TD3: 2 lines x 44 (passports); MRV-A: 2 x 44 starting with "V" (visa)

Pure text processing, never raises: returns None when no parseable MRZ is found.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

_LENGTHS = {30: 3, 36: 2, 44: 2}
_FILLER_LOOKALIKES = str.maketrans(
    {"«": "<", "‹": "<", "»": "<", "›": "<", "＜": "<", ">": "<", "{": "<", "(": "<"}
)
_TO_DIGIT = str.maketrans({"O": "0", "Q": "0", "D": "0", "U": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8", "G": "6", "<": "0"})
_TO_ALPHA = str.maketrans({"0": "O", "1": "I", "2": "Z", "5": "S", "8": "B", "6": "G"})
_MRZ_CHARS = re.compile(r"[A-Z0-9<]+")
_WEIGHTS = (7, 3, 1)


def find_mrz(text: str) -> Optional[dict[str, Any]]:
    """Returns the best-validated MRZ found in `text`, or None."""
    if not text:
        return None
    try:
        best: Optional[dict[str, Any]] = None
        for lines in _candidate_groups(text):
            parsed = _parse_lines(lines)
            if parsed is None:
                continue
            if best is None or _score(parsed) > _score(best):
                best = parsed
        return best
    except Exception:
        return None


_FIELD_ALIASES = {
    "document_number": {
        "passportno", "passportnumber", "documentno", "documentnumber", "docno", "docnumber",
        "idno", "idnumber", "cardno", "cardnumber", "visano", "visanumber",
    },
    "birth_date": {"dateofbirth", "birthdate", "dob"},
    "expiry_date": {"dateofexpiry", "expirydate", "expirationdate", "expiry", "validuntil", "validtill"},
    "surname": {"surname", "lastname", "familyname"},
    "given_names": {"givenname", "givennames", "firstname", "firstnames", "forename", "forenames"},
    "sex": {"sex", "gender"},
    "nationality": {"nationality"},
    "issuing_country": {"issuingcountry", "countryofissue", "issuingstate"},
}
_CHECKED_FIELDS = {"document_number", "birth_date", "expiry_date"}
_NAME_FIELDS = {"surname", "given_names"}


def apply_mrz_to_fields(fields: list[dict[str, Any]], mrz: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    """Corrects requested fields from a fully valid MRZ (the LLM may garble values it read).

    Check-digit-verified fields always take the MRZ value; names only get their casing repaired;
    other fields are only filled when empty.
    """
    if not mrz or not mrz.get("valid"):
        return fields
    out = []
    for field in fields:
        key = re.sub(r"[^a-z]", "", str(field.get("name") or "").lower())
        mrz_key = next((k for k, aliases in _FIELD_ALIASES.items() if key in aliases), None)
        mrz_value = mrz.get(mrz_key) if mrz_key else None
        value = field.get("value")
        if mrz_value:
            corrected = mrz_value if value in (None, "") else _corrected(mrz_key, str(value), str(mrz_value))
            if corrected is not None:
                field = {**field, "value": corrected}
        out.append(field)
    return out


def _corrected(mrz_key: str, value: str, mrz_value: str) -> Optional[str]:
    """Returns the value to use instead of `value`, or None to keep it."""
    if mrz_key in _CHECKED_FIELDS:
        return mrz_value
    if mrz_key in _NAME_FIELDS:
        # Names carry no check digit (OCR misreads like I -> T pass validation), so a different
        # printed name is kept; only garbled casing like "ERIKSSon" is repaired.
        letters = re.sub(r"[^A-Z]", "", value.upper())
        mrz_letters = re.sub(r"[^A-Z]", "", mrz_value.upper())
        if letters == mrz_letters and value not in (value.upper(), value.title()):
            return value.upper()
    return None


def check_digit(value: str) -> int:
    total = 0
    for i, ch in enumerate(value):
        if ch.isdigit():
            n = int(ch)
        elif "A" <= ch <= "Z":
            n = ord(ch) - 55
        else:
            n = 0
        total += n * _WEIGHTS[i % 3]
    return total % 10


def _clean_line(line: str) -> str:
    return re.sub(r"\s+", "", line.upper().translate(_FILLER_LOOKALIKES))


def _normalize_length(line: str) -> Optional[str]:
    """Snaps a line to the nearest MRZ length (30/36/44), padding lost trailing fillers."""
    for target in (44, 36, 30):
        if len(line) == target:
            return line
    for target in (44, 36, 30):
        if 0 < target - len(line) <= 3 and line.endswith("<"):
            return line + "<" * (target - len(line))
    return None


def _candidate_lines(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for idx, raw in enumerate(text.splitlines()):
        line = _clean_line(raw)
        if "<" not in line or not _MRZ_CHARS.fullmatch(line):
            continue
        # OCR sometimes merges the MRZ lines into one.
        for length, count in _LENGTHS.items():
            if len(line) == length * count and count > 1:
                out.extend((idx, line[i * length:(i + 1) * length]) for i in range(count))
                break
        else:
            normalized = _normalize_length(line) if len(line) >= 27 else None
            if normalized:
                out.append((idx, normalized))
            elif 10 <= len(line) < 44 and line[0].isalpha() and line.endswith("<") and "<<" in line:
                # A name line whose trailing fillers OCR dropped; completed from the line below it.
                out.append((idx, line))
    return out


def _candidate_groups(text: str) -> list[list[str]]:
    lines = _candidate_lines(text)
    groups: list[list[str]] = []
    for i in range(len(lines)):
        idx, line = lines[i]
        length = len(line)
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        if (
            nxt
            and len(nxt[1]) in (36, 44)
            and length < len(nxt[1])
            and nxt[0] - idx <= 3
            and line.endswith("<")
            and "<<" in line
        ):
            groups.append([line.ljust(len(nxt[1]), "<"), nxt[1]])
        if length not in _LENGTHS:
            continue
        count = _LENGTHS[length]
        window = lines[i:i + count]
        if len(window) < count:
            continue
        if any(len(line) != length for _, line in window):
            continue
        if window[-1][0] - window[0][0] > 2 * (count - 1) + 1:
            continue
        groups.append([line for _, line in window])
    return groups


def _score(parsed: dict[str, Any]) -> tuple[int, int]:
    checks = parsed["checks"]
    return (int(parsed["valid"]), sum(1 for ok in checks.values() if ok))


def _parse_lines(lines: list[str]) -> Optional[dict[str, Any]]:
    length = len(lines[0])
    if length == 30 and len(lines) == 3:
        return _parse_td1(lines)
    if length == 36 and len(lines) == 2:
        return _parse_two_line(lines, "MRV-B" if lines[0].startswith("V") else "TD2")
    if length == 44 and len(lines) == 2:
        return _parse_two_line(lines, "MRV-A" if lines[0].startswith("V") else "TD3")
    return None


def _digits(value: str) -> str:
    return value.translate(_TO_DIGIT)


def _alpha(value: str) -> str:
    return value.translate(_TO_ALPHA)


def _check(value: str, digit_char: str) -> bool:
    digit = _digits(digit_char)
    return digit.isdigit() and check_digit(value) == int(digit)


def _names(field: str) -> tuple[str, str]:
    field = _alpha(field).strip("<")
    surname, _, given = field.partition("<<")
    return surname.replace("<", " ").strip(), re.sub(r"<+", " ", given).strip()


def _date(yymmdd: str, *, expiry: bool) -> Optional[str]:
    if not yymmdd.isdigit() or len(yymmdd) != 6:
        return None
    yy, mm, dd = int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:])
    this_year = date.today().year % 100
    if expiry:
        century = 1900 if yy >= 70 and yy > this_year + 30 else 2000
    else:
        century = 1900 if yy > this_year else 2000
    try:
        return date(century + yy, mm, dd).isoformat()
    except ValueError:
        return None


def _sex(ch: str) -> str:
    return {"M": "M", "F": "F"}.get(ch, "X")


def _text(value: str) -> str:
    return value.replace("<", " ").strip()


def _parse_two_line(lines: list[str], fmt: str) -> Optional[dict[str, Any]]:
    l1, l2 = lines
    width = len(l1)
    if not l1[0].isalpha():
        return None
    doc_type = _alpha(l1[0:2]).replace("<", "")
    issuing = _alpha(l1[2:5])
    surname, given = _names(l1[5:width])

    number = l2[0:9]
    birth = _digits(l2[13:19])
    expiry = _digits(l2[21:27])
    # Composite check digits run over the repaired numeric fields.
    l2 = l2[0:9] + _digits(l2[9]) + l2[10:13] + birth + _digits(l2[19]) + l2[20] + expiry + _digits(l2[27]) + l2[28:]
    checks = {
        "document_number": _check(number, l2[9]),
        "birth_date": _check(birth, l2[19]),
        "expiry_date": _check(expiry, l2[27]),
    }
    result: dict[str, Any] = {
        "format": fmt,
        "document_type": doc_type,
        "issuing_country": issuing.replace("<", ""),
        "surname": surname,
        "given_names": given,
        "document_number": number.replace("<", ""),
        "nationality": _alpha(l2[10:13]).replace("<", ""),
        "birth_date": _date(birth, expiry=False),
        "sex": _sex(l2[20]),
        "expiry_date": _date(expiry, expiry=True),
    }
    if fmt == "TD3":
        personal = l2[28:42]
        result["personal_number"] = _text(personal)
        checks["personal_number"] = _check(personal, l2[42])
        checks["composite"] = _check(l2[0:10] + l2[13:20] + l2[21:43], l2[43])
    elif fmt == "TD2":
        result["optional_data"] = _text(l2[28:35])
        checks["composite"] = _check(l2[0:10] + l2[13:20] + l2[21:35], l2[35])
    else:
        result["optional_data"] = _text(l2[28:width])
    return _finish(result, checks, lines)


def _parse_td1(lines: list[str]) -> Optional[dict[str, Any]]:
    l1, l2, l3 = lines
    if not l1[0].isalpha():
        return None
    number, number_cd, optional1 = l1[5:14], l1[14], l1[15:30]
    extension = optional1.split("<", 1)[0] if number_cd == "<" else ""
    if extension:
        # Document numbers longer than 9 chars continue in the optional field.
        number, number_cd = number + extension[:-1], extension[-1]
        optional1 = optional1[len(extension):]
    birth = _digits(l2[0:6])
    expiry = _digits(l2[8:14])
    l2 = birth + _digits(l2[6]) + l2[7] + expiry + _digits(l2[14]) + l2[15:]
    surname, given = _names(l3)
    checks = {
        "document_number": _check(number, number_cd),
        "birth_date": _check(birth, l2[6]),
        "expiry_date": _check(expiry, l2[14]),
        "composite": _check(l1[5:30] + l2[0:7] + l2[8:15] + l2[18:29], l2[29]),
    }
    result = {
        "format": "TD1",
        "document_type": _alpha(l1[0:2]).replace("<", ""),
        "issuing_country": _alpha(l1[2:5]).replace("<", ""),
        "surname": surname,
        "given_names": given,
        "document_number": number.replace("<", ""),
        "nationality": _alpha(l2[15:18]).replace("<", ""),
        "birth_date": _date(birth, expiry=False),
        "sex": _sex(l2[7]),
        "expiry_date": _date(expiry, expiry=True),
        "optional_data": _text(optional1 + "<" + l2[18:29]),
    }
    return _finish(result, checks, lines)


def _finish(result: dict[str, Any], checks: dict[str, bool], lines: list[str]) -> Optional[dict[str, Any]]:
    # Fewer than a third of the check digits passing means this was not really an MRZ.
    if sum(checks.values()) * 3 < len(checks):
        return None
    result["checks"] = checks
    result["valid"] = all(checks.values())
    result["raw_lines"] = lines
    return result
