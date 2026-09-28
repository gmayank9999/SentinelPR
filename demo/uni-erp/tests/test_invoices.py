from datetime import date
from decimal import Decimal

import pytest

from unierp.core.domain import Enrollment
from unierp.core.errors import ValidationError
from unierp.fees.invoices import generate_invoice, outstanding_balance, record_payment

ISSUED = date(2026, 8, 1)


@pytest.fixture
def invoice(store):
    store.add_enrollment(Enrollment("S1", "MA101", "2026-FALL"))
    return generate_invoice(store, "S1", "2026-FALL", ISSUED)


def test_invoice_totals(invoice):
    assert invoice.total == Decimal("7500") + Decimal("1500") + Decimal("800")
    assert invoice.due_date == date(2026, 8, 31)


def test_unpaid_balance_before_due(store, invoice):
    balance = outstanding_balance(store, invoice.id, date(2026, 8, 20))
    assert balance.status == "unpaid"
    assert balance.outstanding == invoice.total


def test_balance_accrues_late_fee(store, invoice):
    # 10 days late -> two started weeks -> 4% on 9800.
    balance = outstanding_balance(store, invoice.id, date(2026, 9, 10))
    assert balance.payable == Decimal("10192")


def test_full_payment_on_time(store, invoice):
    record_payment(store, invoice.id, invoice.total, date(2026, 8, 25))
    balance = outstanding_balance(store, invoice.id, date(2026, 12, 1))
    assert balance.status == "paid"
    assert balance.outstanding == 0


def test_partial_payment(store, invoice):
    record_payment(store, invoice.id, Decimal("5000"), date(2026, 8, 25))
    assert outstanding_balance(store, invoice.id, date(2026, 8, 26)).status == "partial"


def test_payment_must_be_positive(store, invoice):
    with pytest.raises(ValidationError):
        record_payment(store, invoice.id, Decimal("0"), ISSUED)
