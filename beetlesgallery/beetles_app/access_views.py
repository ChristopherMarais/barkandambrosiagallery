"""Views for requesting and granting access (see beetles_app/access.py)."""
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.views import PasswordResetConfirmView, PasswordResetView
from django.db.models.functions import Lower
from django.shortcuts import redirect, render
from django.urls import reverse_lazy

from . import access
from .forms import AccessRequestForm
from .models import AccessRequest
from .views import superuser_required

RECENT_DECISIONS = 25


def request_access(request):
    """Public form. The person is told the same thing whether or not a request was already waiting."""
    signed_in = request.user.is_authenticated
    if request.method == "POST":
        if request.POST.get("leave_blank"):  # the hidden field: a bot
            return redirect("request_access_sent")
        form = AccessRequestForm(request.POST, signed_in=signed_in)
        if form.is_valid():
            if access.throttled(request):
                form.add_error(None, "There have been too many requests just now. Please try again in an hour.")
            else:
                access.submit_request(request, form.cleaned_data)
                return redirect("request_access_sent")
    else:
        initial = {}
        if signed_in:
            initial = {"name": request.user.get_full_name(), "email": request.user.email}
        form = AccessRequestForm(initial=initial, signed_in=signed_in)
    return render(request, "accounts/request_access.html", {"form": form, "areas": access.AREAS, "signed_in": signed_in})


def request_access_sent(request):
    return render(request, "accounts/request_access_sent.html")


def verify_email(request, uidb64, token):
    """The link in the confirmation email: the address is theirs, so the approvers are told."""
    access_request = access.confirm_email(request, uidb64, token)
    return render(request, "accounts/email_confirmed.html", {"access_request": access_request})


class ResetRequestView(PasswordResetView):
    """'Forgot your password?': emails a link to choose a new one. Only active accounts with that email get it."""

    template_name = "accounts/password_reset_form.html"
    email_template_name = "accounts/password_reset_email.txt"
    html_email_template_name = "accounts/password_reset_email.html"
    subject_template_name = "accounts/password_reset_subject.txt"
    success_url = reverse_lazy("password_reset_done")

    def form_valid(self, form):
        from django.core.cache import cache
        key = f"password-reset:{self.request.META.get('REMOTE_ADDR', '')}"
        count = cache.get(key, 0)
        if count >= 10:   # an hour's worth is plenty; say nothing different so it cannot be probed
            return redirect(self.success_url)
        cache.set(key, count + 1, 3600)
        return super().form_valid(form)


def password_reset_done(request):
    return render(request, "accounts/password_reset_done.html")


class SetPasswordView(PasswordResetConfirmView):
    """The link in a password-reset email: the person chooses a new password."""

    template_name = "accounts/password_set.html"
    success_url = reverse_lazy("login")
    post_reset_login = False

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Your new password is set. You can sign in now.")
        return response


def _with_context(requests):
    """Attach the readable areas, the role they need and any account already using the email."""
    users = {}
    emails = {r.email.lower() for r in requests}
    for user in get_user_model().objects.annotate(email_lower=Lower("email")).filter(email_lower__in=emails):
        users.setdefault(user.email_lower, []).append(user)
    for r in requests:
        r.area_labels = access.area_labels(r.areas)
        r.wanted = set(access.extras(r.areas))
        r.existing_users = [u for u in users.get(r.email.lower(), []) if u != r.user]
    return requests


@superuser_required
def access_requests(request):
    if request.method == "POST":
        try:
            decision = access.decide(
                request.POST.get("request_id"), request.POST.get("decision"), request.user,
                request.POST.get("note"), request.build_absolute_uri, areas=request.POST.getlist("areas"),
            )
        except access.AccessError as exc:
            messages.error(request, str(exc))
        else:
            who = decision.access_request.name
            if decision.access_request.status == AccessRequest.Status.DENIED:
                messages.success(request, f"Denied {who}'s request.")
            else:
                granted = ", ".join(access.area_labels(decision.granted)) or "nothing more (Basic)"
                messages.success(request, f"{who}: granted {granted}.")
            if decision.email_error:
                messages.error(request, f"{who} could not be emailed ({decision.email_error}). Tell them their account is ready: username {decision.user.username}.")
        return redirect("access_requests")

    pending = list(
        AccessRequest.objects.filter(status=AccessRequest.Status.PENDING, email_verified_at__isnull=False)
        .select_related("user").order_by("created_at")
    )
    decided = list(
        AccessRequest.objects.exclude(status=AccessRequest.Status.PENDING)
        .select_related("decided_by", "user").order_by("-decided_at")[:RECENT_DECISIONS]
    )
    return render(request, "beetles/access_requests.html", {
        "pending": _with_context(pending),
        "decided": decided,
        "areas": access.AREAS,
    })
