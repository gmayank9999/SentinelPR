"""Invoices and payments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from unierp.core.domain import Invoice, Payment
from unierp.core.errors import ValidationError
from unierp.core.store import Store
from unierp.fees.calculator import apply_late_fee, term_fee_breakdown

PAYMENT_TERMS = timedelta(days=30)


def generate_invoice(store: Store, student_id: str, term: str, issued_on: date) -> Invoice:
    student = store.get_student(student_id)
    items = term_fee_breakdown(student, store.enrollments_for(student_id), store.catalog, term)
    invoice_id = f"INV-{term}-{student_id}"
    invoice = Invoice(
        id=invoice_id,
        student_id=student_id,
        term=term,
        issued_on=issued_on,
        due_date=issued_on + PAYMENT_TERMS,
        items=items,
    )
    with store.lock:
        store.invoices[invoice_id] = invoice
    return invoice


def record_payment(store: Store, invoice_id: str, amount: Decimal, paid_on: date) -> Payment:
    store.get_invoice(invoice_id)
    if amount <= 0:
        raise ValidationError("payment amount must be positive")
    payment = Payment(invoice_id=invoice_id, amount=amount, paid_on=paid_on)
    with store.lock:
        store.payments.append(payment)
    return payment


@dataclass
class Balance:
    invoice_id: str
    billed: Decimal
    payable: Decimal
    paid: Decimal

    @property
    def outstanding(self) -> Decimal:
        return max(self.payable - self.paid, Decimal("0"))

    @property
    def status(self) -> str:
        if self.paid == 0:
            return "unpaid"
        return "paid" if self.outstanding == 0 else "partial"


def outstanding_balance(store: Store, invoice_id: str, as_of: date) -> Balance:
    """Balance on ``as_of``. Late fees accrue on the invoice total once the due date passes."""
    invoice = store.get_invoice(invoice_id)
    payments = store.payments_for(invoice_id)
    paid = sum((p.amount for p in payments), Decimal("0"))
    settled_on = max((p.paid_on for p in payments), default=as_of)
    reference = as_of if paid < invoice.total else settled_on
    payable = apply_late_fee(invoice.total, invoice.due_date, reference)
    return Balance(invoice_id=invoice_id, billed=invoice.total, payable=payable, paid=paid)
