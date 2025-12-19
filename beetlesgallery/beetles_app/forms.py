from django import forms
from django.contrib.auth.forms import UserCreationForm, PasswordChangeForm
from django.contrib.auth import get_user_model

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
        fields = ["first_name", "last_name", "email"]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "First name", "autocomplete": "given-name"}),
            "last_name": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Last name", "autocomplete": "family-name"}),
            "email": forms.EmailInput(attrs={"class": TAILWIND_INPUT, "placeholder": "name@example.com", "autocomplete": "email"}),
        }

class TailwindUserCreationForm(UserCreationForm):
    # These fields ensure the widgets render with the correct Tailwind classes
    username = forms.CharField(
        widget=forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Choose a username", "autocomplete": "username", "autofocus": "autofocus"})
    )
    email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Email (optional)", "autocomplete": "email"})
    )
    password1 = forms.CharField(
        widget=forms.PasswordInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Password", "autocomplete": "new-password"})
    )
    password2 = forms.CharField(
        widget=forms.PasswordInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Re-type password", "autocomplete": "new-password"})
    )

    class Meta:
        model = User
        fields = ("username", "email")

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

class UpdateBatchUploadForm(forms.Form):
    file = forms.FileField(
        label="Upload XLSX file",
        help_text="Must include a header row. Blank cells mean 'no change'.",
        widget=forms.ClearableFileInput(attrs={"class": TAILWIND_INPUT, "accept": ".xlsx"}),
    )

    def clean_file(self):
        f = self.cleaned_data["file"]
        name = (f.name or "").lower()
        if not name.endswith(".xlsx"):
            raise forms.ValidationError("Please upload an .xlsx workbook.")
        # Optional: 10 MB size guard
        if getattr(f, "size", 0) > 10 * 1024 * 1024:
            raise forms.ValidationError("File is too large (max 10 MB).")
        return f