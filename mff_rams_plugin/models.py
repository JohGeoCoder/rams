import math
from datetime import timedelta

from residue import CoerceUTF8 as UnicodeText, UTCDateTime
from sqlalchemy import and_, or_
from sqlalchemy.types import Integer
from sqlalchemy.ext.hybrid import hybrid_property

from uber.models import Session
from uber.config import c
from uber.utils import localized_now, localize_datetime
from uber.models.types import Choice, DefaultColumn as Column
from uber.decorators import cost_property, presave_adjustment


@Session.model_mixin
class SessionMixin:
    def all_panelists(self):
        return self.query(Attendee).filter(or_(
            Attendee.ribbon.contains(c.PANELIST_RIBBON),
            Attendee.ribbon == c.STAFF_RIBBON,
            Attendee.badge_type == c.GUEST_BADGE))\
            .order_by(Attendee.full_name).all()


@Session.model_mixin
class Group:
    power = Column(Integer, default=0)
    power_fee = Column(Integer, default=0)
    power_usage = Column(UnicodeText)
    location = Column(UnicodeText, default='', admin_only=True)
    table_fee = Column(Integer, default=0)
    tax_number = Column(UnicodeText)

    @presave_adjustment
    def guest_groups_approved(self):
        if self.leader and self.leader.badge_type == c.GUEST_BADGE and self.status == c.UNAPPROVED:
            self.status = c.APPROVED

    @cost_property
    def power_cost(self):
        return self.power_fee if self.power_fee \
            else c.POWER_PRICES[int(self.power)]

    @cost_property
    def table_cost(self):
        return self.table_fee if self.table_fee \
            else c.TABLE_PRICES[int(self.tables)]

    @property
    def dealer_payment_due(self):
        if self.approved:
            return self.approved + timedelta(c.DEALER_PAYMENT_DAYS)

    @property
    def dealer_payment_is_late(self):
        if self.approved:
            return localized_now() > localize_datetime(self.dealer_payment_due)

    @presave_adjustment
    def dealers_add_badges(self):
        if self.is_dealer and self.is_new:
            self.can_add = True

    @property
    def tables_repr(self):
        return c.TABLE_OPTS[int(self.tables) - 1][1] if self.tables \
            else "No Table"

    @property
    def dealer_max_badges(self):
        return c.MAX_DEALERS or min(math.ceil(self.tables) * 3, 12)


@Session.model_mixin
class Attendee:
    comped_reason = Column(UnicodeText, default='', admin_only=True)
    fursuiting = Column(Choice(c.FURSUITING_OPTS))

    @presave_adjustment
    def save_group_cost(self):
        if self.group and self.group.auto_recalc:
            self.group.cost = self.group.default_cost

    @presave_adjustment
    def never_spam(self):
        self.can_spam = False

    @presave_adjustment
    def not_attending_need_not_pay(self):
        if self.badge_status == c.NOT_ATTENDING:
            self.paid = c.NEED_NOT_PAY
            self.comped_reason = "Automated: Not Attending badge status."

    @presave_adjustment
    def print_ready_before_event(self):
        if c.PRE_CON:
            if self.badge_status == c.COMPLETED_STATUS \
                    and not self.is_not_ready_to_checkin \
                    and self.times_printed < 1 \
                    and self.ribbon != c.STAFF_RIBBON:
                self.print_pending = True

    @presave_adjustment
    def reprint_prereg_name_change(self):
        if self.times_printed >= 1 and not self.orig_value_of('checked_in') and \
                        self.orig_value_of('badge_printed_name') != self.badge_printed_name:
            self.print_pending = True
            self.for_review += "Automated message: Badge marked for free reprint " \
                               "because we think this is a preregistered attendee who wanted a different badge name."

    @presave_adjustment
    def _staffing_badge_and_ribbon_adjustments(self):
        if self.badge_type == c.STAFF_BADGE or c.STAFF_RIBBON in self.ribbon_ints:
            self.ribbon = remove_opt(self.ribbon_ints, c.VOLUNTEER_RIBBON)

        elif self.staffing and self.badge_type != c.STAFF_BADGE \
                and c.STAFF_RIBBON not in self.ribbon_ints and c.VOLUNTEER_RIBBON not in self.ribbon_ints:
            self.ribbon = add_opt(self.ribbon_ints, c.VOLUNTEER_RIBBON)

        if self.badge_type == c.STAFF_BADGE or c.STAFF_RIBBON in self.ribbon_intsm:
            self.staffing = True
            if not self.overridden_price and self.paid in [c.NOT_PAID, c.PAID_BY_GROUP]:
                self.paid = c.NEED_NOT_PAY

    @cost_property
    def badge_cost(self):
        registered = self.registered_local if self.registered else None
        if self.paid == c.NEED_NOT_PAY \
                and self.badge_type not in [c.SPONSOR_BADGE, c.SHINY_BADGE]:
            return 0
        elif self.paid == c.NEED_NOT_PAY:
            return c.BADGE_TYPE_PRICES[self.badge_type] \
                   - c.get_attendee_price(registered)
        elif self.overridden_price is not None:
            return self.overridden_price
        elif self.badge_type == c.ONE_DAY_BADGE:
            return c.get_oneday_price(registered)
        elif self.is_presold_oneday:
            return max(0, c.get_presold_oneday_price(self.badge_type) + self.age_discount)
        if self.badge_type in c.BADGE_TYPE_PRICES:
            return int(c.BADGE_TYPE_PRICES[self.badge_type])
        elif self.age_discount != 0:
            return max(0, c.get_attendee_price(registered) + self.age_discount)
        else:
            return c.get_attendee_price(registered)

    @property
    def age_discount(self):
        if 'val' in self.age_group_conf \
                and self.age_group_conf['val'] == c.UNDER_13 \
                and c.AT_THE_CON:
            if self.badge_type == c.ATTENDEE_BADGE:
                discount = 33
            elif self.badge_type in [c.FRIDAY, c.SUNDAY]:
                discount = 13
            elif self.badge_type == c.SATURDAY:
                discount = 20
            if not self.age_group_conf['discount'] \
                    or self.age_group_conf['discount'] < discount:
                return -discount
        return -self.age_group_conf['discount']

    @property
    def paid_for_a_shirt(self):
        return self.badge_type in [c.SPONSOR_BADGE, c.SHINY_BADGE]

    @property
    def staffing_or_will_be(self):
        return self.staffing or self.badge_type == c.STAFF_BADGE \
               or c.VOLUNTEER_RIBBON in self.ribbon_ints or c.STAFF_RIBBON in self.ribbon_ints
