"""
Sign-in that ignores the case of the username. Phones capitalise the first letter of a text box, so
"Chris" for the account "chris" used to fail as a wrong password. Sign-up already refuses a username that
differs from another only in case; if old accounts ever do clash, only the exact spelling signs in.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class CaseInsensitiveModelBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()
        if username is None:
            username = kwargs.get(User.USERNAME_FIELD)
        if username:
            matches = list(User._default_manager.filter(**{f"{User.USERNAME_FIELD}__iexact": username})
                           .values_list(User.USERNAME_FIELD, flat=True)[:2])
            if len(matches) == 1:
                username = matches[0]
        return super().authenticate(request, username=username, password=password, **kwargs)
