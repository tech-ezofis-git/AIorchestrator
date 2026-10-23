import pytest

from app.ftl.quote_estimator.rfq_master import (
    apply_rfq_master_addresses,
    overlay_master_addresses,
    pick_master_row,
)


ATTA = {
    "Email": "jhouse@attaelevators.com",
    "Company_Name": "ATTA Elevators",
    "Billing_Address": "40 North Rivermede Rd., Concord, ON L4K 2H3",
    "Shipping_Address": "120 Bloor Street East, Toronto, ON",
}
MARCH = {
    "Email": "jr.lewis@march-elevator.ca",
    "Company_Name": "March Elevator Limited",
    "Billing_Address": "1025 Stacey Court, Mississauga, ON L4W 2X7",
    "Shipping_Address": "1025 Stacey Court, Mississauga, ON L4W 2X7",
}


def _quote(**overrides):
    data = {
        "customer_name": "ATTA Elevators",
        "email": "jhouse@attaelevators.com",
        "billing_address": "40 North Rivermede Rd.",
        "shipping_address": "120 Bloor Street East",
        "assumptions": [],
    }
    data.update(overrides)
    return data


def test_email_match_overlays_bill_and_ship():
    out = overlay_master_addresses(_quote(), [ATTA, MARCH])
    assert out["billing_address"] == ATTA["Billing_Address"]
    assert out["shipping_address"] == ATTA["Shipping_Address"]
    assert out["rfq_master_match"] == "RFQ Master"
    assert out["billing_address_from_master"] is True
    assert out["shipping_address_from_master"] is True


def test_company_match_when_email_missing():
    quote = _quote(email="", customer_name="March Elevator")
    out = overlay_master_addresses(quote, [ATTA, MARCH])
    assert out["billing_address"] == MARCH["Billing_Address"]
    assert out["shipping_address"] == MARCH["Shipping_Address"]


def test_domain_match():
    quote = _quote(email="estimator@attaelevators.com", customer_name="")
    out = overlay_master_addresses(quote, [ATTA])
    assert out["billing_address"] == ATTA["Billing_Address"]


def test_fetched_address_match():
    quote = _quote(
        email="",
        customer_name="",
        billing_address="1025 Stacey Court, Mississauga",
        shipping_address="",
    )
    out = overlay_master_addresses(quote, [ATTA, MARCH])
    assert out["billing_address"] == MARCH["Billing_Address"]
    assert out["shipping_address"] == MARCH["Shipping_Address"]


def test_no_master_row_keeps_eml_addresses():
    quote = _quote(
        email="unknown@example.com",
        customer_name="Unknown Contractor",
        billing_address="Jobsite TBD",
        shipping_address="145 Richmond Ave W",
    )
    out = overlay_master_addresses(quote, [ATTA, MARCH])
    assert out["billing_address"] == "Jobsite TBD"
    assert out["shipping_address"] == "145 Richmond Ave W"
    assert "rfq_master_match" not in out
    assert out["billing_address_from_master"] is False
    assert out["shipping_address_from_master"] is False


def test_empty_master_field_keeps_eml():
    row = {
        "Email": "jhouse@attaelevators.com",
        "Company_Name": "ATTA Elevators",
        "Billing_Address": "40 North Rivermede Rd., Concord, ON L4K 2H3",
        "Shipping_Address": "",
    }
    quote = _quote(shipping_address="120 Bloor Street East, Toronto, ON")
    out = overlay_master_addresses(quote, [row])
    assert out["billing_address"] == row["Billing_Address"]
    assert out["shipping_address"] == "120 Bloor Street East, Toronto, ON"
    assert out["billing_address_from_master"] is True
    assert out["shipping_address_from_master"] is False


def test_skips_ftl_forwarder_and_uses_inner_customer_email():
    quote = _quote(email="")
    candidate = (
        "From: John Lewis <john@ftl-distribution.ca>\n"
        "The real customer's email, further down in the body below, "
        "is: Julie House <jhouse@attaelevators.com>."
    )
    row = pick_master_row([ATTA, MARCH], quote, candidate)
    assert row is ATTA


@pytest.mark.asyncio
async def test_apply_without_tenant_keeps_quote():
    quote = _quote()
    out = await apply_rfq_master_addresses(quote, tenant_id=None)
    assert out["billing_address"] == quote["billing_address"]
    assert out["shipping_address"] == quote["shipping_address"]
    assert out["billing_address_from_master"] is False
    assert out["shipping_address_from_master"] is False


@pytest.mark.asyncio
async def test_apply_with_supplied_rows():
    out = await apply_rfq_master_addresses(_quote(), rows=[ATTA])
    assert out["billing_address"] == ATTA["Billing_Address"]
    assert out["billing_address_from_master"] is True
    assert out["shipping_address_from_master"] is True
