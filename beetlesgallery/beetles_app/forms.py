from django import forms
from django.contrib.auth.forms import UserCreationForm, PasswordChangeForm
from django.contrib.auth import get_user_model

from . import access

User = get_user_model()

# Updated styles to match your HTML template exactly (Dark mode + Padding)
TAILWIND_INPUT = (
    "w-full rounded-lg border border-stroke bg-transparent "
    "py-4 pl-6 pr-10 outline-none "
    "focus:border-primary focus-visible:shadow-none "
    "dark:border-form-strokedark dark:bg-form-input dark:focus:border-primary"
)

class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name"]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "First name", "autocomplete": "given-name"}),
            "last_name": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Last name", "autocomplete": "family-name"}),
        }

class TailwindUserCreationForm(UserCreationForm):
    # These fields ensure the widgets render with the correct Tailwind classes
    username = forms.CharField(
        widget=forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Choose a username", "autocomplete": "username", "autofocus": "autofocus"})
    )
    password1 = forms.CharField(
        widget=forms.PasswordInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Password", "autocomplete": "new-password"})
    )
    password2 = forms.CharField(
        widget=forms.PasswordInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Re-type password", "autocomplete": "new-password"})
    )

    class Meta:
        model = User
        fields = ("username",)

class PasswordChangeFormStyled(PasswordChangeForm):
    """Same form as Django’s, but with visible, styled inputs."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        placeholders = {
            "old_password": "Current password",
            "new_password1": "New password",
            "new_password2": "Re-type new password",
        }
        for name, field in self.fields.items():
            field.widget.attrs.update({
                "class": TAILWIND_INPUT,
                "placeholder": placeholders.get(name, ""),
                "autocomplete": "new-password" if name != "old_password" else "current-password",
            })

class ValidSpeciesUploadForm(forms.Form):
    csv_file = forms.FileField(
        label="valid_species.csv",
        help_text="UTF-8 CSV with required headers."
    )
    label = forms.CharField(
        label="Reference label",
        required=False,
        help_text='Shown to users (e.g., "2025-10-14 18:58 UTC"). Leave blank to use file mtime.'
    )

class DescribedNamesUploadForm(forms.Form):
    csv_file = forms.FileField(
        label="described_names.csv",
        help_text="UTF-8 CSV with required headers."
    )
    label = forms.CharField(
        label="Reference label",
        required=False,
        help_text='Shown to users (e.g., "2025-10-14 18:58 UTC"). Leave blank to use file mtime.'
    )

class UpdateBatchUploadForm(forms.Form):
    file = forms.FileField(
        label="Upload CSV file",
        help_text="Must include a header row. Blank cells mean 'no change'.",
        widget=forms.ClearableFileInput(attrs={"class": TAILWIND_INPUT, "accept": ".csv"}),
    )

    def clean_file(self):
        f = self.cleaned_data["file"]
        name = (f.name or "").lower()
        if not name.endswith(".csv"):
            raise forms.ValidationError("Please upload a .csv file.")
        # Optional: 10 MB size guard
        if getattr(f, "size", 0) > 10 * 1024 * 1024:
            raise forms.ValidationError("File is too large (max 10 MB).")
        return f

class AccessRequestForm(forms.Form):
    """The Sign up form; signed in, it asks for more access (see beetles_app/access.py)."""

    name = forms.CharField(label="Your name", max_length=200)
    email = forms.EmailField(label="Email", max_length=254)
    affiliation = forms.CharField(label="Institution or affiliation", max_length=200)
    # Every account is Basic once the email is confirmed; these are the extras a curator reviews (optional).
    areas = forms.MultipleChoiceField(
        label="Anything more you'd like?",
        choices=[(key, label) for key, label, _ in access.AREAS],
        widget=forms.CheckboxSelectMultiple, required=False,
    )
    reason = forms.CharField(
        label="What will you use it for?", max_length=2000, widget=forms.Textarea(attrs={"rows": 4}), required=False,
    )
    # Someone without an account chooses their own (they are left out when asking while signed in).
    username = forms.CharField(label="Username", max_length=150, required=False)
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput, required=False)
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput, required=False)
    # Hidden from people; a bot that fills every field fills this one too.
    leave_blank = forms.CharField(required=False, widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}))

    def __init__(self, *args, signed_in=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.signed_in = signed_in
        if signed_in:
            for name in ("username", "password1", "password2"):
                del self.fields[name]

    def clean_username(self):
        username = self.cleaned_data.get("username", "").strip()
        if not username:
            raise forms.ValidationError("Choose a username.")
        User.username_validator(username)
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("That username is taken. Please choose another.")
        return username

    def clean(self):
        data = super().clean()
        if self.signed_in:
            return data
        email, p1, p2 = data.get("email"), data.get("password1"), data.get("password2")
        if email and User.objects.filter(email__iexact=email, is_active=True).exists():
            self.add_error("email", "An account with this email already exists. Sign in, or use \"Forgot your password?\" on the sign-in page.")
        if not p1:
            self.add_error("password1", "Choose a password.")
        elif p1 != p2:
            self.add_error("password2", "The two passwords do not match.")
        else:
            from django.contrib.auth.password_validation import validate_password
            try:
                validate_password(p1, User(username=data.get("username", ""), email=email or ""))
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return data

    def clean_name(self):
        return " ".join(self.cleaned_data["name"].split())  # one line: it goes in an email subject

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()

    def clean_affiliation(self):
        return " ".join(self.cleaned_data["affiliation"].split())
