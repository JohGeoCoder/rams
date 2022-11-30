from datetime import datetime, timedelta
import stripe

from pytz import UTC
from pockets.autolog import log
from residue import JSON, CoerceUTF8 as UnicodeText, UTCDateTime, UUID
from sqlalchemy import and_, func, or_, select

from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.schema import ForeignKey
from sqlalchemy.types import Integer
from sqlalchemy.orm import backref

from uber.config import c
from uber.custom_tags import format_currency
from uber.models import MagModel
from uber.models.attendee import Attendee, AttendeeAccount, Group
from uber.models.types import default_relationship as relationship, Choice, DefaultColumn as Column
from uber.utils import Charge


__all__ = [
    'ArbitraryCharge', 'MerchDiscount', 'MerchPickup', 'ModelReceipt', 'MPointsForCash',
    'NoShirt', 'OldMPointExchange', 'ReceiptItem', 'ReceiptTransaction', 'Sale']


class ArbitraryCharge(MagModel):
    amount = Column(Integer)
    what = Column(UnicodeText)
    when = Column(UTCDateTime, default=lambda: datetime.now(UTC))
    reg_station = Column(Integer, nullable=True)

    _repr_attr_names = ['what']


class MerchDiscount(MagModel):
    """Staffers can apply a single-use discount to any merch purchases."""
    attendee_id = Column(UUID, ForeignKey('attendee.id'), unique=True)
    uses = Column(Integer)


class MerchPickup(MagModel):
    picked_up_by_id = Column(UUID, ForeignKey('attendee.id'))
    picked_up_for_id = Column(UUID, ForeignKey('attendee.id'), unique=True)
    picked_up_by = relationship(
        Attendee,
        primaryjoin='MerchPickup.picked_up_by_id == Attendee.id',
        cascade='save-update,merge,refresh-expire,expunge')
    picked_up_for = relationship(
        Attendee,
        primaryjoin='MerchPickup.picked_up_for_id == Attendee.id',
        cascade='save-update,merge,refresh-expire,expunge')


class MPointsForCash(MagModel):
    attendee_id = Column(UUID, ForeignKey('attendee.id'))
    amount = Column(Integer)
    when = Column(UTCDateTime, default=lambda: datetime.now(UTC))


class NoShirt(MagModel):
    """
    Used to track when someone tried to pick up a shirt they were owed when we
    were out of stock, so that we can contact them later.
    """
    attendee_id = Column(UUID, ForeignKey('attendee.id'), unique=True)


class OldMPointExchange(MagModel):
    attendee_id = Column(UUID, ForeignKey('attendee.id'))
    amount = Column(Integer)
    when = Column(UTCDateTime, default=lambda: datetime.now(UTC))


class Sale(MagModel):
    attendee_id = Column(UUID, ForeignKey('attendee.id', ondelete='set null'), nullable=True)
    what = Column(UnicodeText)
    cash = Column(Integer, default=0)
    mpoints = Column(Integer, default=0)
    when = Column(UTCDateTime, default=lambda: datetime.now(UTC))
    reg_station = Column(Integer, nullable=True)
    payment_method = Column(Choice(c.SALE_OPTS), default=c.MERCH)


"""
Attendees and groups have a running receipt that has items and transactions added to it dynamically.

Receipt items can be purchases or credits added to the receipt. They do not involve money changing hands.

Receipt transactions keep track of payments and refunds, along with the method (e.g., cash, Stripe, Square)
and, if applicable, the Stripe ID for that transaction. In some cases, such as during prereg or when an attendee
pays for their art show application and badge at the same time, there may be multiple receipt transactions for one Stripe ID.
"""
class ModelReceipt(MagModel):
    invoice_num = Column(Integer, default=0)
    owner_id = Column(UUID, index=True)
    owner_model = Column(UnicodeText)
    closed = Column(UTCDateTime, nullable=True)

    @property
    def all_sorted_items_and_txns(self):
        return sorted(self.receipt_items + self.receipt_txns, key=lambda x: x.added)

    @property
    def open_receipt_items(self):
        return [item for item in self.receipt_items if not item.closed]

    @property
    def closed_receipt_items(self):
        return [item for item in self.receipt_items if item.closed]

    @property
    def charge_description_list(self):
        return ", ".join([item.desc + " x" + str(item.count) for item in self.open_receipt_items if item.amount > 0])

    @property
    def cancelled_txns(self):
        return [txn for txn in self.receipt_txns if txn.cancelled]

    @property
    def pending_txns(self):
        return [txn for txn in self.receipt_txns if txn.is_pending_charge]

    @property
    def pending_total(self):
        return sum([txn.amount for txn in self.receipt_txns if txn.is_pending_charge])

    @hybrid_property
    def payment_total(self):
        return sum([txn.receipt_share for txn in self.receipt_txns if not txn.cancelled and (txn.charge_id or txn.method != c.STRIPE and txn.amount > 0)])
    
    @payment_total.expression
    def payment_total(cls):
        return select([func.sum(ReceiptTransaction.amount)]
                     ).where(ReceiptTransaction.receipt_id == cls.id
                     ).where(ReceiptTransaction.cancelled == None
                     ).where(or_(ReceiptTransaction.charge_id != None,
                                and_(ReceiptTransaction.method != c.STRIPE, ReceiptTransaction.amount > 0))
                     ).label('payment_total')

    @hybrid_property
    def refund_total(self):
        return sum([txn.amount for txn in self.receipt_txns if txn.amount < 0]) * -1

    @refund_total.expression
    def refund_total(cls):
        return select([func.sum(ReceiptTransaction.amount) * -1]
                     ).where(and_(ReceiptTransaction.amount < 0, ReceiptTransaction.receipt_id == cls.id)
                     ).label('refund_total')

    @property
    def current_amount_owed(self):
        return max(0, self.current_receipt_amount)

    @property
    def current_receipt_amount(self):
        return self.item_total - self.txn_total

    @property
    def item_total(self):
        return sum([(item.amount * item.count) for item in self.receipt_items])

    @property
    def txn_total(self):
        return self.payment_total - self.refund_total

    @property
    def total_str(self):
        return "{} in {} - {} in {} = {} owe {}".format(format_currency(self.item_total / 100),
                                                        "Purchases" if self.item_total >= 0 else "Credit",
                                                        format_currency(self.txn_total / 100),
                                                        "Payments" if self.txn_total >= 0 else "Refunds",
                                                        "They" if self.current_receipt_amount >= 0 else "We",
                                                        format_currency(self.current_receipt_amount / 100))

    @property
    def last_incomplete_txn(self):
        return sorted(self.pending_txns, key=lambda t: t.added, reverse=True)[0] if self.pending_txns else None


class ReceiptTransaction(MagModel):
    receipt_id = Column(UUID, ForeignKey('model_receipt.id', ondelete='SET NULL'), nullable=True)
    receipt = relationship('ModelReceipt', foreign_keys=receipt_id,
                           cascade='save-update, merge',
                           backref=backref('receipt_txns', cascade='save-update, merge'))
    intent_id = Column(UnicodeText)
    charge_id = Column(UnicodeText)
    refund_id = Column(UnicodeText)
    method = Column(Choice(c.PAYMENT_METHOD_OPTS), default=c.STRIPE)
    amount = Column(Integer)
    refunded = Column(Integer, nullable=True)
    added = Column(UTCDateTime, default=lambda: datetime.now(UTC))
    cancelled = Column(UTCDateTime, nullable=True)
    who = Column(UnicodeText)
    desc = Column(UnicodeText)

    @property
    def receipt_share(self):
        # I'm saving this for later
        # return sum([item.total for item in self.receipt_items])
            
        return min(self.amount, sum([item.total_amount for item in self.receipt.receipt_items if item.added <= self.added]))

    def update_amount_refunded(self):
        if not self.intent_id:
            return
        
        refunded_total = 0
        for refund in stripe.Refund.list(payment_intent=self.intent_id):
            refunded_total += refund.amount
        
        self.refunded = refunded_total

    @property
    def is_pending_charge(self):
        return self.intent_id and not self.charge_id and not self.cancelled

    @property
    def stripe_id(self):
        # Return the most relevant Stripe ID for admins
        return self.charge_id or self.intent_id

    def get_stripe_intent(self):
        if not self.stripe_id:
            return

        try:
            return stripe.PaymentIntent.retrieve(self.intent_id)
        except Exception as e:
            log.error(e)

    def check_paid_from_stripe(self):
        if self.charge_id:
            return

        intent = self.get_stripe_intent()
        if intent and intent.charges:
            return Charge.mark_paid_from_intent_id(self.intent_id, intent.charges.data[0].id)


class ReceiptItem(MagModel):
    receipt_id = Column(UUID, ForeignKey('model_receipt.id', ondelete='SET NULL'), nullable=True)
    receipt = relationship('ModelReceipt', foreign_keys=receipt_id,
                           cascade='save-update, merge',
                           backref=backref('receipt_items', cascade='save-update, merge'))
    """
    We don't want these columns yet, but I will later
    txn_id = Column(UUID, ForeignKey('receipt_transaction.id', ondelete='SET NULL'), nullable=True)
    receipt_txn = relationship('ReceiptTransaction', foreign_keys=txn_id,
                           cascade='save-update, merge',
                           backref=backref('receipt_items', cascade='save-update, merge'))"""
    amount = Column(Integer)
    count = Column(Integer, default=1)
    added = Column(UTCDateTime, default=lambda: datetime.now(UTC))
    closed = Column(UTCDateTime, nullable=True)
    who = Column(UnicodeText)
    desc = Column(UnicodeText)
    revert_change = Column(JSON, default={}, server_default='{}')

    @property
    def total_amount(self):
        return self.amount * self.count


