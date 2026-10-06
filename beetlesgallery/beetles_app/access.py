"""
Accounts and requests for more access.

Someone fills in /accounts/request-access/ with who they are, the username and password they want and, optionally,
what more than a Basic account they would like (and why). They get an inactive account and an email with a link to
confirm their address. Confirming it **activates the account straight away as Basic** (areas.py): the image
browser, taxonomy browser, interactions page, AI classifier and Beetle ID game.

If they asked for more, the request stays open for a superuser on My Account -> Access Requests, who grants the
areas they think right, one by one (or none). Their Basic account works meanwhile. Someone already signed in can
ask for more the same way; the approvers are told straight away. Superuser is never granted through a request.
"""
import logging
from datetime import timedelta
from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from .models import AccessRequest

logger = logging.getLogger(__name__)

from . import areas as site_areas

BASIC, DENY = "basic", "deny"
BASIC_SUMMARY = f"the image browser, the taxonomy browser, the interactions page, the AI classifier and the game ({settings.GAME_DISPLAY_NAME})"
# What can be asked for beyond Basic: the areas (key, label, description)
AREAS = site_areas.AREAS
AREA_LABELS = dict(site_areas.LABELS)
# keys used on the form before Basic accounts (old requests still show readable labels)
AREA_LABELS.update({"browse": "Browse and download images", "classify": "AI species classifier", "game": settings.GAME_DISPLAY_NAME})


THROTTLE_PER_IP = 5      # requests per hour from one address
THROTTLE_IN_TOTAL = 30   # requests per hour in all, so approvers' inboxes cannot be flooded
THROTTLE_WINDOW = 60 * 60


class AccessError(Exception):
    """A problem to show the approver; nothing was changed."""


def area_labels(keys):
    return [AREA_LABELS[k] for k in keys if k in AREA_LABELS]


def extras(keys):
    """The requested areas a superuser has to decide on (everything beyond Basic)."""
    return [k for k in keys if k in site_areas.KEYS]


def throttled(request):
    """True when this address (or everyone together) has sent too many requests in the last hour."""
    from django.core.cache import cache

    keys = {
        f"access-request:ip:{request.META.get('REMOTE_ADDR', '')}": THROTTLE_PER_IP,
        "access-request:all": THROTTLE_IN_TOTAL,
    }
    counts = {key: cache.get(key, 0) for key in keys}
    if any(counts[key] >= limit for key, limit in keys.items()):
        return True
    for key, count in counts.items():
        cache.set(key, count + 1, THROTTLE_WINDOW)
    return False


def absolute_url(request, name, *args):
    return request.build_absolute_uri(reverse(name, args=args))


def send_email(subject, template, context, to, reply_to=None):
    """Send templates/emails/<template>.txt with <template>.html as its formatted version. Raises on failure."""
    context = {**context, "subject": subject}
    message = EmailMultiAlternatives(
        subject=subject, body=render_to_string(f"emails/{template}.txt", context), to=to, reply_to=reply_to,
    )
    message.attach_alternative(render_to_string(f"emails/{template}.html", context), "text/html")
    message.send(fail_silently=False)


# ---------------------------------------------------------------------------
# A request arrives
# ---------------------------------------------------------------------------
def verification_url(request, user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    return request.build_absolute_uri(reverse("verify_email", args=[uid, default_token_generator.make_token(user)]))


def send_verification_email(access_request, url):
    """Email the applicant a link that proves the address is theirs. Returns an error text, or ''."""
    days = settings.PASSWORD_RESET_TIMEOUT // (24 * 60 * 60)
    try:
        send_email(
            "Please confirm your email for the Bark & Ambrosia Beetle Gallery", "verify",
            {"name": access_request.name, "url": url, "days": days}, [access_request.email],
        )
    except Exception as exc:
        logger.exception("Could not send the confirmation email for access request %s", access_request.pk)
        return f"{type(exc).__name__}: {exc}"[:255]
    return ""


def submit_request(request, data):
    """
    Save a request from the form's cleaned_data.

    Someone without an account gets an inactive one with the username and password they chose, and is emailed
    a link to confirm their address; the approvers are told once it is confirmed. Someone already signed in asking
    for more access is already confirmed, so the approvers are told straight away.
    Returns the AccessRequest, or None when this email already has a request waiting (the confirmation email is
    sent again, to that address only).
    """
    User = get_user_model()
    review_url = absolute_url(request, "access_requests")
    signed_in = request.user.is_authenticated
    # A request made before people chose their own password has no account attached and can never be approved:
    # a new one for the same email replaces it.
    AccessRequest.objects.filter(
        email__iexact=data["email"], status=AccessRequest.Status.PENDING, user__isnull=True
    ).update(status=AccessRequest.Status.DENIED, decision_note="Replaced by a newer request.", decided_at=timezone.now())
    try:
        with transaction.atomic():
            if signed_in:
                user, verified = request.user, timezone.now()
            else:
                user = User(username=data["username"], email=data["email"], first_name=data["name"][:150], is_active=False)
                user.set_password(data["password1"])
                user.save()
                verified = None
            access_request = AccessRequest.objects.create(
                name=data["name"], email=data["email"], affiliation=data["affiliation"], reason=data["reason"],
                areas=list(data["areas"]), user=user, email_verified_at=verified,
            )
    except IntegrityError:
        waiting = AccessRequest.objects.filter(
            email__iexact=data["email"], status=AccessRequest.Status.PENDING, email_verified_at__isnull=True,
            user__is_active=False,
        ).select_related("user").first()
        if waiting:
            error = send_verification_email(waiting, verification_url(request, waiting.user))
            waiting.notify_error = f"Confirmation email failed: {error}" if error else ""
            waiting.save(update_fields=["notify_error"])
        return None
    if signed_in:
        notify_approvers(access_request, review_url)
    else:
        error = send_verification_email(access_request, verification_url(request, user))
        if error:
            access_request.notify_error = f"Confirmation email failed: {error}"
            access_request.save(update_fields=["notify_error"])
    return access_request


def confirm_email(request, uidb64, token):
    """The link in the confirmation email. Returns the AccessRequest (now confirmed), or None for a bad or old link."""
    from django.utils.http import urlsafe_base64_decode

    User = get_user_model()
    try:
        user = User.objects.get(pk=urlsafe_base64_decode(uidb64).decode())
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        return None
    if not default_token_generator.check_token(user, token):
        return None
    access_request = AccessRequest.objects.filter(user=user, status=AccessRequest.Status.PENDING).first()
    if access_request is None:
        return None
    if access_request.email_verified_at is None:
        access_request.email_verified_at = timezone.now()
        access_request.save(update_fields=["email_verified_at"])
        open_basic_account(access_request, request.build_absolute_uri)
    return access_request


def open_basic_account(access_request, absolute_uri):
    """
    The address is confirmed: the account works at once as Basic. With nothing more asked for, the request is done;
    otherwise it stays open and the approvers are told. The applicant is emailed either way.
    """
    user = access_request.user
    if not user.is_active:
        user.is_active = True
        user.save(update_fields=["is_active"])
    wanted = extras(access_request.areas)
    if not wanted:
        access_request.status = AccessRequest.Status.APPROVED
        access_request.granted_role = BASIC
        access_request.decided_at = timezone.now()
        access_request.decision_note = "Basic account, approved automatically when the email was confirmed."
        access_request.save(update_fields=["status", "granted_role", "decided_at", "decision_note"])
    else:
        notify_approvers(access_request, absolute_uri(reverse("access_requests")))
    try:
        send_email("Welcome to the Bark & Ambrosia Beetle Gallery!", "approved", {
            "name": access_request.name, "username": user.username, "summary": BASIC_SUMMARY,
            "waiting": area_labels(wanted), "granted": [], "note": "",
            "login_url": absolute_uri(reverse("login")), "reset_url": absolute_uri(reverse("password_reset")),
        }, [access_request.email])
    except Exception:
        logger.exception("Could not send the welcome email for access request %s", access_request.pk)


def approver_recipients():
    """The configured addresses, plus every active superuser who has one: all superusers can decide requests."""
    recipients = list(settings.ACCESS_REQUEST_RECIPIENTS)
    known = {r.lower() for r in recipients}
    for address in get_user_model().objects.filter(is_superuser=True, is_active=True).exclude(email="").values_list("email", flat=True):
        if address.lower() not in known:
            known.add(address.lower())
            recipients.append(address)
    return recipients


def send_reminder(review_url, older_than_hours=24, dry_run=False):
    """
    One email to the approvers listing the requests that have waited for a decision longer than ``older_than_hours``
    (counted from when the applicant confirmed their email). Returns the requests listed; sends nothing if there are none.
    """
    cutoff = timezone.now() - timedelta(hours=older_than_hours)
    waiting = list(
        AccessRequest.objects.filter(status=AccessRequest.Status.PENDING, email_verified_at__isnull=False,
                                     email_verified_at__lte=cutoff).order_by("email_verified_at")
    )
    recipients = approver_recipients()
    if not waiting or not recipients or dry_run:
        return waiting, recipients
    now = timezone.now()
    rows = []
    for r in waiting:
        hours = int((now - r.email_verified_at).total_seconds() // 3600)
        rows.append({
            "name": r.name, "email": r.email, "areas": ", ".join(area_labels(r.areas)) or "nothing selected",
            "waited": f"{hours // 24} days" if hours >= 48 else f"{hours} hours",
        })
    count = len(waiting)
    send_email(
        f"Reminder: {count} access request{'s' if count != 1 else ''} waiting", "reminder",
        {"count": count, "waiting": rows, "review_url": review_url}, recipients,
    )
    return waiting, recipients


def notify_approvers(access_request, review_url):
    recipients = approver_recipients()
    if not recipients:
        access_request.notify_error = "No approvers are configured (ACCESS_REQUEST_RECIPIENTS)."
    else:
        context = {
            "name": access_request.name, "email": access_request.email, "affiliation": access_request.affiliation,
            "areas": area_labels(access_request.areas), "reason": access_request.reason, "review_url": review_url,
        }
        try:
            send_email(
                f"New access request from {access_request.name}", "new_request", context,
                recipients, reply_to=[access_request.email],
            )
            access_request.notified_at = timezone.now()
        except Exception as exc:  # a mail problem must not lose the request
            logger.exception("Could not email the approvers about access request %s", access_request.pk)
            access_request.notify_error = f"{type(exc).__name__}: {exc}"[:255]
    access_request.save(update_fields=["notified_at", "notify_error"])


# ---------------------------------------------------------------------------
# A request is decided
# ---------------------------------------------------------------------------
@dataclass
class Decision:
    access_request: AccessRequest
    user: object = None
    email_error: str = ""
    granted: list = None


def _grant(access_request, areas, decided_by):
    """Activate the requester's account if needed and grant these areas (adding to what they have, never removing)."""
    from .models import AreaGrant

    user = access_request.user
    if user is None:
        raise AccessError("This request has no account attached.")
    if access_request.email_verified_at is None:
        raise AccessError(f"{access_request.name} has not confirmed their email address yet.")
    if not user.is_active:
        user.is_active = True
        user.save(update_fields=["is_active"])
    for area in areas:
        AreaGrant.objects.get_or_create(user=user, area=area, defaults={"granted_by": decided_by})
    return user


def _discard_unused_account(access_request):
    """A denied applicant's account that never worked (made for this request, never signed in) is removed."""
    user = access_request.user
    if user is not None and not user.is_active and user.last_login is None and not user.is_staff:
        user.delete()


def decide(request_id, choice, decided_by, note, absolute_uri, areas=None):
    """
    Decide an open request: ``choice`` "grant" gives the account the chosen ``areas`` (any of areas.KEYS; none means
    it stays Basic), "deny" gives nothing more (a working account stays Basic). Then the applicant is emailed.
    ``absolute_uri`` is request.build_absolute_uri. Raises AccessError with a message for the approver.
    """
    if choice not in ("grant", DENY):
        raise AccessError("Choose what to grant, or Deny.")
    areas = [a for a in (areas or []) if a in site_areas.KEYS]
    note = (note or "").strip()[:1000]

    with transaction.atomic():
        try:
            access_request = AccessRequest.objects.select_for_update().get(pk=request_id)
        except (AccessRequest.DoesNotExist, ValidationError, ValueError):
            raise AccessError("That request no longer exists.")
        if access_request.status != AccessRequest.Status.PENDING:
            raise AccessError(f"{access_request.name}'s request was already {access_request.status}.")
        user = None if choice == DENY else _grant(access_request, areas, decided_by)
        access_request.status = AccessRequest.Status.DENIED if choice == DENY else AccessRequest.Status.APPROVED
        access_request.granted_role = "" if choice == DENY else ("areas" if areas else BASIC)
        access_request.granted_areas = [] if choice == DENY else areas
        access_request.decided_by = decided_by
        access_request.decided_at = timezone.now()
        access_request.decision_note = note
        access_request.save()
        applicant = access_request.user
        if choice == DENY:
            _discard_unused_account(access_request)

    decision = Decision(access_request, user=user or applicant)
    decision.granted = areas if choice != DENY else []
    try:
        _email_applicant(decision, absolute_uri(reverse("login")), absolute_uri(reverse("password_reset")))
    except Exception as exc:  # the decision stands; the approver is told to pass it on
        logger.exception("Could not email the applicant for access request %s", access_request.pk)
        decision.email_error = f"{type(exc).__name__}: {exc}"[:255]
    return decision


def _email_applicant(decision, login_url, reset_url):
    access_request = decision.access_request
    context = {"name": access_request.name, "note": access_request.decision_note}
    if access_request.status == AccessRequest.Status.DENIED:
        context["keeps_account"] = decision.user is not None and decision.user.is_active
        send_email("About your Bark & Ambrosia Beetle Gallery request", "denied", context, [access_request.email])
        return
    context.update(summary=BASIC_SUMMARY, granted=area_labels(decision.granted), waiting=[],
                   username=decision.user.username, login_url=login_url, reset_url=reset_url)
    send_email("Your Bark & Ambrosia Beetle Gallery access", "approved", context, [access_request.email])
