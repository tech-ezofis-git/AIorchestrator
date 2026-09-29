"""OCR agent MRZ: detection/parsing of TD1/TD2/TD3/MRV-A/MRV-B and merge into the OCR JSON."""
import json

import pytest

from app.agents.ocr_agent import _pick_mrz
from app.agents.ocr_helpers import PageSelection
from app.config import Settings
from app.integrations.mrz_image import find_mrz_crops
from app.integrations.mrz_parse import apply_mrz_to_fields, check_digit, find_mrz
from app.integrations.ocr_engine import OcrEngineClient, embedded_pdf_text_is_usable

# ICAO 9303 specimen MRZs.
TD3 = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\nL898902C36UTO7408122F1204159ZE184226B<<<<<10"
TD1 = "I<UTOD231458907<<<<<<<<<<<<<<<\n7408122F1204159UTO<<<<<<<<<<<6\nERIKSSON<<ANNA<MARIA<<<<<<<<<<"
TD2 = "I<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<\nD231458907UTO7408122F1204159<<<<<<<6"
MRV_A = "V<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\nL8988901C4XXX4009078F96121096ZE184226B<<<<<<"
MRV_B = "V<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<\nL8988901C4XXX4009078F9612109<<<<<<<<"


def test_check_digit():
    assert check_digit("L898902C3") == 6
    assert check_digit("740812") == 2
    assert check_digit("<<<<") == 0


@pytest.mark.parametrize(
    ("text", "fmt", "number"),
    [
        (TD3, "TD3", "L898902C3"),
        (TD1, "TD1", "D23145890"),
        (TD2, "TD2", "D23145890"),
        (MRV_A, "MRV-A", "L8988901C"),
        (MRV_B, "MRV-B", "L8988901C"),
    ],
)
def test_formats_parse_and_validate(text, fmt, number):
    mrz = find_mrz(text)
    assert mrz is not None
    assert mrz["format"] == fmt
    assert mrz["document_number"] == number
    assert mrz["surname"] == "ERIKSSON"
    assert mrz["given_names"] == "ANNA MARIA"
    assert mrz["sex"] == "F"
    assert mrz["valid"] is True
    assert all(mrz["checks"].values())


def test_td3_fields():
    mrz = find_mrz(TD3)
    assert mrz["document_type"] == "P"
    assert mrz["issuing_country"] == "UTO"
    assert mrz["nationality"] == "UTO"
    assert mrz["birth_date"] == "1974-08-12"
    assert mrz["expiry_date"] == "2012-04-15"
    assert mrz["personal_number"] == "ZE184226B"
    assert mrz["raw_lines"] == TD3.split("\n")


def test_mrz_inside_noisy_ocr_text():
    text = (
        "REPUBLIC OF UTOPIA\nPASSPORT\nSurname ERIKSSON\n"
        "P<UTOERIKSSON<<ANNA<MARIA«<<<<<<<<<<<<<<<\n\n"
        "L898902C36UTO74O8122F12O4159ZE184226B<<<<< 10\n"
    )
    mrz = find_mrz(text)
    assert mrz["format"] == "TD3"
    assert mrz["birth_date"] == "1974-08-12"
    assert mrz["valid"] is True


def test_merged_lines_are_split():
    mrz = find_mrz(TD3.replace("\n", ""))
    assert mrz["format"] == "TD3"
    assert mrz["valid"] is True


def test_name_line_missing_fillers_and_angle_brackets_are_repaired():
    name_line, data_line = TD3.split("\n")
    text = f"{name_line.rstrip('<')}<<\n{data_line.replace('<', '>')}"
    mrz = find_mrz(text)
    assert mrz["format"] == "TD3"
    assert mrz["surname"] == "ERIKSSON"
    assert mrz["given_names"] == "ANNA MARIA"
    assert mrz["raw_lines"][0] == name_line
    assert mrz["valid"] is True


def test_short_filler_line_without_data_line_is_ignored():
    assert find_mrz("P<UTOERIKSSON<<ANNA<MARIA<<\nSome other text") is None


def test_pdf_text_layer_with_valid_mrz_is_kept():
    text = f"Sample data\nSurname ERIKSSON\nMachine Readable Zone\n{TD3}"
    assert embedded_pdf_text_is_usable(text) is True


def test_passport_pdf_uses_text_layer_not_remote_ocr():
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    for i, line in enumerate(["Sample data", "Surname ERIKSSON", *TD3.split("\n")]):
        page.insert_text((40, 60 + i * 20), line, fontname="cour", fontsize=10)
    text = OcrEngineClient()._extract_local_text(
        data=doc.tobytes(),
        filename="passport.pdf",
        content_type="application/pdf",
        page_selection=PageSelection(start=1, end=1, raw="1"),
    )
    assert text is not None
    assert find_mrz(text)["valid"] is True


def _passport_png(rotate=None) -> bytes:
    import cv2
    import fitz
    import numpy as np

    doc = fitz.open()
    page = doc.new_page(width=600, height=420)
    page.insert_text((40, 50), "UTOPIA  PASSPORT", fontsize=18)
    for i, line in enumerate(["Surname: ERIKSSON", "Given names: ANNA MARIA", "Date of birth: 12 AUG 1974"]):
        page.insert_text((40, 90 + i * 22), line, fontsize=12)
    page.draw_rect(fitz.Rect(440, 80, 560, 220), color=(0, 0, 0), fill=(0.7, 0.7, 0.7))
    for i, line in enumerate(TD3.split("\n")):
        page.insert_text((30, 350 + i * 24), line, fontname="cour", fontsize=12.5)
    pix = page.get_pixmap(dpi=200, colorspace=fitz.csGRAY)
    image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    if rotate is not None:
        image = cv2.rotate(image, rotate)
    return cv2.imencode(".png", image)[1].tobytes()


_PAGE_1 = PageSelection(start=1, end=1, raw="1")


@pytest.mark.parametrize("rotation", [None, "ROTATE_90_CLOCKWISE", "ROTATE_180"], ids=["upright", "sideways", "upside_down"])
def test_mrz_band_is_located_on_page_image(rotation):
    import cv2
    import numpy as np

    png = _passport_png(getattr(cv2, rotation) if rotation else None)
    crops = find_mrz_crops(png, filename="passport.png", content_type="image/png", page_selection=_PAGE_1)
    assert len(crops) == 1
    crop = cv2.imdecode(np.frombuffer(crops[0]["png"], np.uint8), cv2.IMREAD_GRAYSCALE)
    height, width = crop.shape
    assert width > 4 * height  # a wide band holding only the MRZ lines, not the whole page


def test_invoice_has_no_mrz_band():
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((40, 60), "INVOICE INV-2026-001", fontsize=16)
    for i in range(20):
        page.insert_text((40, 100 + i * 18), f"Item {i}   Widget {i}   Qty 2   Unit 12.50   Total 25.00", fontsize=10)
    page.draw_line((40, 480), (560, 480))
    crops = find_mrz_crops(doc.tobytes(), filename="inv.pdf", content_type="application/pdf", page_selection=_PAGE_1)
    assert crops == []


async def test_engine_reads_mrz_crop_and_retries_rotated(monkeypatch):
    calls: list[str] = []

    async def fake_extract(self, *, filename, **_kwargs):
        calls.append(filename)
        if filename != "mrz.png":
            return "UTOPIA PASSPORT\nSurname ERIKSSON"
        # First read of the crop is garbage (upside-down scan); the 180-degree re-read is clean.
        return ">>>>>>9L0t80tLLE >>>" if calls.count("mrz.png") == 1 else TD3

    monkeypatch.setattr(OcrEngineClient, "_call_extract_text", fake_extract)
    engine = OcrEngineClient(Settings(ocr_extract_url="http://paddle.invalid/extract"))
    result = await engine.run_ocr(
        file_bytes=_passport_png(), filename="passport.png", content_type="image/png",
        page_selection=_PAGE_1, scan_mrz=True,
    )
    assert result["mrz"]["valid"] is True
    assert result["mrz"]["source"] == "image"
    assert result["mrz"]["page"] == 1
    assert result["mrz"]["document_number"] == "L898902C3"
    assert calls.count("mrz.png") == 2


async def test_engine_skips_mrz_images_when_disabled(monkeypatch):
    async def fake_extract(self, *, filename, **_kwargs):
        assert filename != "mrz.png"
        return "page text"

    monkeypatch.setattr(OcrEngineClient, "_call_extract_text", fake_extract)
    engine = OcrEngineClient(
        Settings(ocr_extract_url="http://paddle.invalid/extract", ocr_mrz_image_enabled=False)
    )
    result = await engine.run_ocr(
        file_bytes=_passport_png(), filename="passport.png", content_type="image/png",
        page_selection=_PAGE_1, scan_mrz=True,
    )
    assert "mrz" not in result


def test_pick_mrz_prefers_valid_image_read_then_text():
    valid = find_mrz(TD3)
    invalid = find_mrz(TD3.replace("F1204159", "F1204158"))
    assert _pick_mrz({**valid, "source": "image"}, valid)["source"] == "image"
    assert _pick_mrz({**invalid, "source": "image"}, valid)["source"] == "text"
    assert _pick_mrz(None, valid)["source"] == "text"
    assert _pick_mrz(None, None) is None


def test_bad_check_digit_is_flagged_not_dropped():
    mrz = find_mrz(TD3.replace("F1204159", "F1204158"))
    assert mrz is not None
    assert mrz["valid"] is False
    assert mrz["checks"]["expiry_date"] is False
    assert mrz["checks"]["document_number"] is True


def test_no_mrz():
    assert find_mrz("Invoice INV-9 Total 100.00\nSOME<<TEXT") is None
    assert find_mrz("") is None
    assert find_mrz("X" * 44 + "\n" + "<" * 44) is None


def _f(name, value, typ="SHORT_TEXT"):
    return {"name": name, "value": value, "type": typ}


def test_apply_mrz_corrects_llm_fields():
    fields = [
        _f("Passport No", "L898902G3"),
        _f("Surname", "ERIKSSon"),
        _f("Given Names", "ANNA MARA"),
        _f("Date of Birth", "1974-12-08", "DATE"),
        _f("Expiry Date", None, "DATE"),
        _f("Nationality", "UTOPIAN"),
        _f("Sex", None),
        _f("Invoice No", "X-1"),
    ]
    out = {f["name"]: f["value"] for f in apply_mrz_to_fields(fields, find_mrz(TD3))}
    assert out == {
        "Passport No": "L898902C3",
        "Surname": "ERIKSSON",
        "Given Names": "ANNA MARA",
        "Date of Birth": "1974-08-12",
        "Expiry Date": "2012-04-15",
        "Nationality": "UTOPIAN",
        "Sex": "F",
        "Invoice No": "X-1",
    }


def test_apply_mrz_keeps_printed_name_forms():
    fields = [_f("Surname", "Eriksson"), _f("First Name", "Anna-Maria")]
    out = apply_mrz_to_fields(fields, find_mrz(TD3))
    assert [f["value"] for f in out] == ["Eriksson", "Anna-Maria"]


def test_apply_mrz_does_not_overwrite_printed_name_with_misread_mrz_name():
    misread = find_mrz(TD3.replace("ERIKSSON", "ERTKSSON"))
    assert misread["valid"] is True  # names carry no check digit
    out = apply_mrz_to_fields([_f("Surname", "ERIKSSON"), _f("Given Names", None)], misread)
    assert [f["value"] for f in out] == ["ERIKSSON", "ANNA MARIA"]


def test_name_line_separated_by_blank_lines_is_completed():
    name_line, data_line = TD3.split("\n")
    mrz = find_mrz(f"{name_line.rstrip('<')}<<\n\n\n{data_line}")
    assert mrz["valid"] is True
    assert mrz["raw_lines"][0] == name_line


async def test_engine_skips_crop_ocr_when_text_mrz_is_valid(monkeypatch):
    calls: list[str] = []

    async def fake_extract(self, *, filename, **_kwargs):
        calls.append(filename)
        return f"UTOPIA PASSPORT\n{TD3}"

    monkeypatch.setattr(OcrEngineClient, "_call_extract_text", fake_extract)
    engine = OcrEngineClient(Settings(ocr_extract_url="http://paddle.invalid/extract"))
    result = await engine.run_ocr(
        file_bytes=_passport_png(), filename="passport.png", content_type="image/png",
        page_selection=_PAGE_1, scan_mrz=True,
    )
    assert calls == ["passport.png"]
    assert "mrz" not in result
    assert find_mrz(result["text"])["valid"] is True


def test_apply_mrz_skips_invalid_mrz():
    fields = [_f("Passport No", "WRONG")]
    invalid = find_mrz(TD3.replace("F1204159", "F1204158"))
    assert apply_mrz_to_fields(fields, invalid) == fields
    assert apply_mrz_to_fields(fields, None) == fields


def _fake_llm(monkeypatch, seen: list[str]):
    async def fake_completion(self, messages, **_kwargs):
        seen.append(json.dumps(messages))
        return {
            "content": json.dumps(
                {"ocrResult": [{"name": "Passport No", "value": "L898902C3", "type": "SHORT_TEXT"}]}
            ),
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_completion)


def _post_passport(client):
    return client.post(
        "/chat",
        data={
            "session_id": "s-mrz",
            "intent": "ocr",
            "pageno": "1",
            "parameters": json.dumps(["Passport No,SHORT_TEXT"]),
            "tableparameters": "[]",
        },
        files={"file": ("passport.txt", f"PASSPORT\n{TD3}\n".encode(), "text/plain")},
    )


def test_ocr_sends_decoded_mrz_to_llm_and_returns_it(client, monkeypatch):
    seen: list[str] = []
    _fake_llm(monkeypatch, seen)

    response = _post_passport(client)

    assert response.status_code == 200, response.text
    body = response.json()
    reply = json.loads(body["reply"])
    assert reply["mrz"]["format"] == "TD3"
    assert reply["mrz"]["document_number"] == "L898902C3"
    assert body["ocr_result"]["mrz"] == reply["mrz"]
    assert reply["ocrResult"][0]["value"] == "L898902C3"
    assert any("MRZ found in the document" in p and "birth_date: 1974-08-12" in p for p in seen)


@pytest.fixture
def mrz_off(monkeypatch):
    monkeypatch.setenv("OCR_MRZ_ENABLED", "false")


def test_ocr_mrz_disabled(mrz_off, client, monkeypatch):
    seen: list[str] = []
    _fake_llm(monkeypatch, seen)

    response = _post_passport(client)

    assert response.status_code == 200, response.text
    assert json.loads(response.json()["reply"])["mrz"] is None
    assert not any("MRZ found in the document" in p for p in seen)
