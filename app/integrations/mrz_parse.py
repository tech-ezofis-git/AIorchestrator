"""Machine Readable Zone (MRZ) detection and parsing from OCR text (ICAO 9303).

Finds MRZ-like lines in extracted text, repairs common OCR confusions, identifies the format and
parses the fields, verifying every check digit:

- TD1: 3 lines x 30 (ID cards)
- TD2: 2 lines x 36 (ID cards / older passports); MRV-B: 2 x 36 starting with "V" (visa)
- TD3: 2 lines x 44 (passports); MRV-A: 2 x 44 starting with "V" (visa)

Pure text processing, never raises: returns None when no parseable MRZ is found.
"""
from __future__ import annotations

import itertools
import re
from datetime import date
from typing import Any, Optional

_LENGTHS = {30: 3, 36: 2, 44: 2}
_MAX_FILLER_REPAIR = 8
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


# ICAO 9303 document codes (first MRZ character).
_DOCUMENT_KINDS = {"P": "Passport", "V": "Visa", "I": "Identity Card", "A": "Identity Card", "C": "Identity Card"}


def mrz_document_kind(mrz: Optional[dict[str, Any]]) -> Optional[str]:
    """Passport / Visa / Identity Card from a fully valid MRZ's document code, else None."""
    if not mrz or not mrz.get("valid"):
        return None
    return _DOCUMENT_KINDS.get(str(mrz.get("document_type") or "")[:1])


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


def _filler_runs(line: str) -> list[tuple[int, int]]:
    """(start, end) of each run of fillers, the trailing run first."""
    runs = [(m.start(), m.end()) for m in re.finditer(r"<+", line)]
    return sorted(runs, key=lambda run: run[1] != len(line))


def _length_variants(line: str) -> list[str]:
    """The line resized to each nearby MRZ length by growing or shrinking one filler run.

    OCR often drops or adds a few fillers inside long "<<<<" runs, shifting every field after them;
    the check digits pick the right variant. Trailing-run edits come first so they win ties.
    """
    if len(line) in _LENGTHS:
        return [line]
    out: list[str] = []
    for target in _LENGTHS:
        diff = target - len(line)
        if abs(diff) > _MAX_FILLER_REPAIR:
            continue
        for start, end in _filler_runs(line):
            size = end - start + diff
            if size >= (0 if end == len(line) else 1):
                out.append(line[:start] + "<" * size + line[end:])
    return list(dict.fromkeys(out))


def _candidate_lines(text: str) -> list[tuple[int, str, list[str]]]:
    """(line index, cleaned line, MRZ-length variants) for every MRZ-like line."""
    out: list[tuple[int, str, list[str]]] = []
    for idx, raw in enumerate(text.splitlines()):
        line = _clean_line(raw)
        if "<" not in line or not _MRZ_CHARS.fullmatch(line):
            continue
        # OCR sometimes merges the MRZ lines into one.
        for length, count in _LENGTHS.items():
            if len(line) == length * count and count > 1:
                out.extend((idx, part, [part]) for part in (line[i * length:(i + 1) * length] for i in range(count)))
                break
        else:
            variants = _length_variants(line)
            if variants or _is_short_name_line(line):
                out.append((idx, line, variants))
    return out


def _is_short_name_line(line: str) -> bool:
    """A name line whose trailing fillers OCR dropped; completed from the line below it."""
    return 10 <= len(line) < 44 and line[0].isalpha() and line.endswith("<") and "<<" in line


def _candidate_groups(text: str) -> list[list[str]]:
    lines = _candidate_lines(text)
    groups: list[list[str]] = []
    for i, (idx, line, variants) in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        if nxt and nxt[0] - idx <= 3 and _is_short_name_line(line):
            for below in nxt[2]:
                if len(below) in (36, 44) and len(line) < len(below):
                    groups.append([line.ljust(len(below), "<"), below])
        for variant in variants:
            length = len(variant)
            count = _LENGTHS[length]
            window = lines[i:i + count]
            if len(window) < count or window[-1][0] - window[0][0] > 2 * (count - 1) + 1:
                continue
            rest = [[v for v in other[2] if len(v) == length] for other in window[1:]]
            groups.extend([variant, *combo] for combo in itertools.product(*rest))
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
        l2 = l2[:42] + _digits(l2[42]) + l2[43:]
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
