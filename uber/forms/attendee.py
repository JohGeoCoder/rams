import re
from datetime import date

from markupsafe import Markup
from pockets.autolog import log
from wtforms import (BooleanField, DateField, EmailField, Form, FormField,
                     HiddenField, SelectField, SelectMultipleField, IntegerField,
                     StringField, TelField, validators, TextAreaField)
from wtforms.widgets import HiddenInput
from wtforms.validators import ValidationError, StopValidation

from uber.config import c
from uber.forms import AddressForm, MultiCheckbox, MagForm, SwitchInput, DollarInput, HiddenIntField
from uber.custom_tags import popup_link
from uber.model_checks import invalid_phone_number
from uber.validations import attendee as attendee_validators

__all__ = ['AdminInfo', 'BadgeExtras', 'PersonalInfo', 'OtherInfo', 'Consents']

class PersonalInfo(AddressForm, MagForm):
    first_name = StringField('First Name', validators=[
        validators.InputRequired("Please provide your first name.")
        ], render_kw={'autocomplete': "fname"})
    last_name = StringField('Last Name', validators=[
        validators.InputRequired("Please provide your last name.")
        ], render_kw={'autocomplete': "lname"})
    same_legal_name = BooleanField('The above name is exactly what appears on my Legal Photo ID.')
    legal_name = StringField('Name as appears on Legal Photo ID', validators=[
        validators.InputRequired("Please provide the name on your photo ID or indicate that your first and last name match your ID.")
        ], render_kw={'placeholder': 'First and last name exactly as they appear on Photo ID'})
    email = EmailField('Email Address', validators=[
        validators.InputRequired("Please enter an email address."),
        validators.Length(max=255, message="Email addresses cannot be longer than 255 characters."),
        validators.Email(granular_message=True),
        ],
        render_kw={'placeholder': 'test@example.com'})
    cellphone = TelField('Phone Number', validators=[
        validators.InputRequired("Please provide a phone number.")
        ], render_kw={'placeholder': 'A phone number we can use to contact you during the event'})
    birthdate = DateField('Date of Birth', validators=[
        validators.InputRequired("Please enter your date of birth.") if c.COLLECT_EXACT_BIRTHDATE else validators.Optional(),
        attendee_validators.attendee_age_checks
        ])
    age_group = SelectField('Age Group', validators=[
        validators.InputRequired("Please select your age group.") if not c.COLLECT_EXACT_BIRTHDATE else validators.Optional()
        ], choices=c.AGE_GROUPS)
    ec_name = StringField('Emergency Contact Name', validators=[
        validators.InputRequired("Please tell us the name of your emergency contact.")
        ], render_kw={'placeholder': 'Who we should contact if something happens to you'})
    ec_phone = TelField('Emergency Contact Phone', validators=[
        validators.InputRequired("Please give us an emergency contact phone number.")
        ], render_kw={'placeholder': 'A valid phone number for your emergency contact'})
    onsite_contact = TextAreaField('Onsite Contact', validators=[
        validators.InputRequired("Please enter contact information for at least one trusted friend onsite, \
                                 or indicate that we should use your emergency contact information instead."),
        validators.Length(max=500, message="You have entered over 500 characters of onsite contact information. \
                          Please provide contact information for fewer friends.")
        ], render_kw={'placeholder': 'Contact info for a trusted friend or friends who will be at or near the venue during the event'})

    copy_email = BooleanField('Use my business email for my personal email.', default=False)
    copy_phone = BooleanField('Use my business phone number for my cellphone number.', default=False)
    copy_address = BooleanField('Use my business address for my personal address.', default=False)

    no_cellphone = BooleanField('I won\'t have a phone with me during the event.')
    no_onsite_contact = BooleanField('My emergency contact is also on site with me at the event.')
    international = BooleanField('I\'m coming from outside the US.')

    def get_optional_fields(self, attendee):
        unassigned_group_reg = attendee.group_id and not attendee.first_name and not attendee.last_name
        valid_placeholder = attendee.placeholder and attendee.first_name and attendee.last_name
        if unassigned_group_reg or valid_placeholder:
            return ['first_name', 'last_name', 'legal_name', 'email', 'birthdate', 'age_group', 'ec_name', 'ec_phone',
                    'address1', 'city', 'region', 'region_us', 'region_canada', 'zip_code', 'country', 'onsite_contact']
        
        optional_list = super().get_optional_fields(attendee)

        if self.same_legal_name.data:
            optional_list.append('legal_name')
        if self.copy_email.data:
            optional_list.append('email')
        if self.copy_phone.data or not attendee.is_dealer:
            optional_list.append('cellphone')
        if self.copy_address.data:
            optional_list.extend(['address1', 'city', 'region', 'region_us', 'region_canada', 'zip_code', 'country'])
        if self.no_onsite_contact.data:
            optional_list.append('onsite_contact')

        return optional_list
    
    def get_non_admin_locked_fields(self, attendee):
        locked_fields = []

        if attendee.is_new or attendee.badge_status == c.PENDING_STATUS:
            return locked_fields
        elif not attendee.is_valid or attendee.badge_status == c.REFUNDED_STATUS:
            return list(self._fields.keys())

        if attendee.placeholder:
            return locked_fields
        
        return locked_fields + ['first_name', 'last_name', 'legal_name', 'same_legal_name']
    
    def validate_onsite_contact(form, field):
        if not field.data and not form.no_onsite_contact.data:
            raise ValidationError('Please enter contact information for at least one trusted friend onsite, \
                                 or indicate that we should use your emergency contact information instead.')
    
    def validate_birthdate(form, field):
        # TODO: Make WTForms use this message instead of the generic DateField invalid value message
        if field.data and not isinstance(field.data, date):
            raise StopValidation('Please use the format YYYY-MM-DD for your date of birth.')
        elif field.data and field.data > date.today():
            raise ValidationError('You cannot be born in the future.')
 
    def validate_cellphone(form, field):
        if field.data and invalid_phone_number(field.data):
            raise ValidationError('Your phone number was not a valid 10-digit US phone number. ' \
                                    'Please include a country code (e.g. +44) for international numbers.')

        if field.data and field.data == form.ec_phone.data:
            raise ValidationError("Your phone number cannot be the same as your emergency contact number.")
        
    def validate_ec_phone(form, field):
        if not form.international.data and invalid_phone_number(field.data):
            if c.COLLECT_FULL_ADDRESS:
                raise ValidationError('Please enter a 10-digit US phone number or include a ' \
                                        'country code (e.g. +44) for your emergency contact number.')
            else:
                raise ValidationError('Please enter a 10-digit emergency contact number.')


class BadgeExtras(MagForm):
    field_aliases = {'badge_type': ['upgrade_badge_type']}

    badge_type = HiddenIntField('Badge Type')
    upgrade_badge_type = HiddenIntField('Badge Type')
    amount_extra = HiddenIntField('Pre-order Merch', validators=[
        validators.NumberRange(min=0, message="Amount extra must be a number that is 0 or higher.")
        ])
    extra_donation = IntegerField('Extra Donation', validators=[
        validators.NumberRange(min=0, message="Extra donation must be a number that is 0 or higher.")
        ], widget=DollarInput(), description=popup_link("../static_views/givingExtra.html", "Learn more"))
    shirt = SelectField('Shirt Size', choices=c.SHIRT_OPTS, coerce=int)
    badge_printed_name = StringField('Name Printed on Badge', validators=[
        validators.Length(max=20, message="Your printed badge name is too long. \
                          Please use less than 20 characters."),
        validators.Regexp(c.VALID_BADGE_PRINTED_CHARS, message="Your printed badge name has invalid characters. \
                          Please use only alphanumeric characters and symbols.")
        ], description="Badge names have a maximum of 20 characters.")
    
    def get_non_admin_locked_fields(self, attendee):
        locked_fields = []

        if attendee.is_new:
            return locked_fields

        if not attendee.is_valid or attendee.badge_status == c.REFUNDED_STATUS:
            return list(self._fields.keys())
        
        if attendee.active_receipt or attendee.badge_status == c.DEFERRED_STATUS:
            locked_fields.extend(['badge_type', 'amount_extra', 'extra_donation'])
        elif not c.BADGE_TYPE_PRICES:
            locked_fields.append('badge_type')
        
        return locked_fields

    def validate_shirt(form, field):
        if (form.amount_extra.data > 0 or form.badge_type.data in c.BADGE_TYPE_PRICES) and field.data == c.NO_SHIRT:
            raise ValidationError("Please select a shirt size.")


class OtherInfo(MagForm):
    placeholder = BooleanField(widget=HiddenInput())
    staffing = BooleanField('I am interested in volunteering!', widget=SwitchInput(), description=popup_link(c.VOLUNTEER_PERKS_URL, "What do I get for volunteering?"))
    requested_dept_ids = SelectMultipleField('Where do you want to help?', choices=c.JOB_INTEREST_OPTS, coerce=int, widget=MultiCheckbox())
    requested_accessibility_services = BooleanField(f'I would like to be contacted by the {c.EVENT_NAME} Accessibility Services department prior to the event and I understand my contact information will be shared with Accessibility Services for this purpose.', widget=SwitchInput())
    interests = SelectMultipleField('What interests you?', choices=c.INTEREST_OPTS, coerce=int, validators=[validators.Optional()], widget=MultiCheckbox())
        
    def get_non_admin_locked_fields(self, attendee):
        locked_fields = [] 

        if not attendee.placeholder:
            # This is an admin field, but we need it on the confirmation page for placeholder attendees
            locked_fields.append('placeholder')

        if attendee.is_new:
            return locked_fields
        
        if not attendee.is_valid or attendee.badge_status == c.REFUNDED_STATUS:
            return list(self._fields.keys())
        
        if attendee.badge_type in [c.STAFF_BADGE, c.CONTRACTOR_BADGE] or attendee.shifts:
            locked_fields.append('staffing')

        return locked_fields


class PreregOtherInfo(OtherInfo):
    promo_code = StringField('Promo Code')
    cellphone = TelField('Phone Number', description="A cellphone number is required for volunteers.", render_kw={'placeholder': 'A phone number we can use to contact you during the event'})
    no_cellphone = BooleanField('I won\'t have a phone with me during the event.')

    def get_non_admin_locked_fields(self, attendee):
        return super().get_non_admin_locked_fields(attendee)


class Consents(MagForm):
    can_spam = BooleanField(f'Please send me emails relating to {c.EVENT_NAME} and {c.ORGANIZATION_NAME} in future years.', description=popup_link("../static_views/privacy.html", "View Our Spam Policy"))
    pii_consent = BooleanField(Markup(f'<strong>Yes</strong>, I understand and agree that {c.ORGANIZATION_NAME} will store the personal information I provided above for the limited purposes of contacting me about my registration'),
                               validators=[validators.InputRequired("You must agree to allow us to store your personal information in order to register.")
                                           ], description=Markup('For more information please check out our <a href="{}" target="_blank">Privacy Policy</a>.'.format(c.PRIVACY_POLICY_URL)))

    def get_non_admin_locked_fields(self, attendee):
        if attendee.needs_pii_consent:
            return []
        
        return ['pii_consent']

    def pii_consent_label(self):
        base_label = f"<strong>Yes</strong>, I understand and agree that {c.ORGANIZATION_NAME} will store the personal information I provided above for the limited purposes of contacting me about my registration"
        label = base_label
        if c.HOTELS_ENABLED:
            label += ', hotel accommodations'
        if c.DONATIONS_ENABLED:
            label += ', donations'
        if c.ACCESSIBILITY_SERVICES_ENABLED:
            label += ', accessibility needs'
        if label != base_label:
            label += ','
        label += ' or volunteer opportunities selected at sign-up.'
        return Markup(label)


class AdminInfo(MagForm):
    placeholder = BooleanField('Placeholder')
    group_id = StringField('Group')