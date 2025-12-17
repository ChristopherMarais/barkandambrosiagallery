from django.contrib.auth import get_user_model

def get_system_user(username: str = "admin"):
    """
    Return the user used for attribution in import jobs when uploaded_by is missing.
    Prefers an existing account named `username`. If not found, creates a minimal,
    inactive service account so attribution never fails.
    """
    User = get_user_model()
    try:
        return User.objects.get(username=username)
    except User.DoesNotExist:
        # Create a minimal, non-loginable service user.
        u = User(
            username=username,
            is_staff=True,        # can view admin if you later enable it
            is_superuser=False,   # keep it limited; change if you want
            is_active=False,      # cannot log in; just used for attribution
        )
        u.set_unusable_password()
        u.save()
        return u
