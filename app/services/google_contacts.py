"""Server-side Google Contacts sync via the People API (OAuth2 auth-code flow).

The legacy "Google Contacts API" was shut down; the supported service is the
People API (https://people.googleapis.com). This module is the unauthenticated
OAuth plumbing -- consent URL, code exchange, token refresh -- plus the
contact mapping. Every function takes explicit arguments so the router can
feed it configuration and the endpoints stay unit-testable.

Unlike Google Sign-In (client ID only), this flow needs the OAuth *client
secret* and an Authorized redirect URI pointing at
/api/auth/google/contacts/callback.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

GOOGLE_CONTACTS_SCOPE = "https://www.googleapis.com/auth/contacts.readonly"
_OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
_PEOPLE_CONNECTIONS_URL = "https://people.googleapis.com/v1/people/me/connections"


class GoogleContactsError(Exception):
    """A Google OAuth/People API failure. `status` is the HTTP status when one
    was returned (401 from the People API means the access token is stale)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _post_form(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise GoogleContactsError(f"Google responded {exc.code}", status=exc.code) from exc
    except Exception as exc:
        raise GoogleContactsError("Could not reach Google") from exc


def build_auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    """The Google consent-screen URL for the authorization-code flow."""
    query = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_CONTACTS_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })
    return f"{_OAUTH_AUTH_URL}?{query}"


def exchange_code(code: str, redirect_uri: str, client_id: str, client_secret: str) -> dict:
    """Swap the authorization code for access + refresh tokens."""
    if not code:
        raise GoogleContactsError("Missing authorization code")
    return _post_form(_OAUTH_TOKEN_URL, {
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
    })


def refresh_access_token(refresh_token: str, client_id: str, client_secret: str) -> dict:
    """Get a fresh access token from a (long-lived) refresh token."""
    return _post_form(_OAUTH_TOKEN_URL, {
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
    })


def fetch_contacts(access_token: str, page_size: int = 1000) -> dict:
    """Fetch the user's contacts via the People API. A 401 response surfaces
    as GoogleContactsError(status=401) so the caller can refresh + retry."""
    query = urllib.parse.urlencode({
        "personFields": "names,emailAddresses,phoneNumbers,addresses",
        "pageSize": page_size,
    })
    req = urllib.request.Request(
        f"{_PEOPLE_CONNECTIONS_URL}?{query}",
        headers={"Authorization": "Bearer " + access_token},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise GoogleContactsError(f"People API responded {exc.code}", status=exc.code) from exc
    except Exception as exc:
        raise GoogleContactsError("Could not reach the People API") from exc


def map_people_to_contacts(people: list | None) -> list:
    """People API person dicts -> {name, email, phone, site_address} dicts in
    the same shape the rest of the contact importers produce."""
    contacts = []
    for person in people or []:
        name = email = phone = address = None
        if person.get("names"):
            name = person["names"][0].get("displayName")
        if person.get("emailAddresses"):
            email = person["emailAddresses"][0].get("value")
        if person.get("phoneNumbers"):
            phone = person["phoneNumbers"][0].get("value")
        if person.get("addresses"):
            address = person["addresses"][0].get("formattedValue")
        if name or email or phone:
            contacts.append({
                "name": (name or "").strip() or None,
                "email": (email or "").strip() or None,
                "phone": (phone or "").strip() or None,
                "site_address": (address or "").strip() or None,
            })
    return contacts
