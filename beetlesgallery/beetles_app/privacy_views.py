"""
The privacy notice at /privacy/ (public). What is collected, Google Analytics as processor, how to withdraw consent.
The contact address comes from settings.PRIVACY_CONTACT_EMAIL (set in .env.prod); the page omits the line when it is empty.
"""
from django.conf import settings
from django.shortcuts import render


def privacy(request):
    return render(request, "privacy.html", {"privacy_contact_email": settings.PRIVACY_CONTACT_EMAIL})
