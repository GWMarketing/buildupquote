"""Browser pages for the BuildUpQuote UI (Jinja2 templates).

Pages are thin shells -- the real data is fetched client-side against the
REST API using the JWT stored in localStorage. A small script in base.html
redirects to /login when no token is present.

The marketing landing page (/) is the one page that does NOT extend
base.html: base.html's auth guard would bounce anonymous visitors to /login,
and the landing page is a standalone document by design (see landing.html).
Its own script redirects authenticated visitors (localStorage bq_token) to
/dashboard, which is how "/ renders for visitors, redirects for users" works
in this client-side-auth architecture.
"""
import os

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from app.auth import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET

router = APIRouter(include_in_schema=False)

_TEMPLATES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"
)
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


@router.get("/")
def home(request: Request):
    """The marketing landing page for anonymous visitors. Logged-in users
    (a bq_token in localStorage) are redirected to /dashboard by landing.html
    itself -- the server can't see localStorage, so the check is client-side,
    consistent with the app's existing auth pattern."""
    return templates.TemplateResponse(request, "landing.html", {"active": ""})


@router.get("/pricing")
def pricing(request: Request):
    """Public pricing page -- also the Stripe checkout cancel destination
    (/pricing?canceled=1 shows an inline notice on the landing page)."""
    return templates.TemplateResponse(request, "landing.html", {"active": ""})


@router.get("/login")
def login(request: Request):
    return templates.TemplateResponse(
        request,
        "login.html",
        {"active": "", "google_client_id": GOOGLE_CLIENT_ID},
    )


@router.get("/register")
def register(request: Request):
    return templates.TemplateResponse(
        request,
        "register.html",
        {"active": "", "google_client_id": GOOGLE_CLIENT_ID},
    )


@router.get("/dashboard")
def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {"active": "dashboard"})


@router.get("/parser")
def parser(request: Request):
    return templates.TemplateResponse(request, "parser.html", {"active": "parser"})


@router.get("/quotes")
def quotes_list(request: Request):
    return templates.TemplateResponse(request, "quotes.html", {"active": "quotes"})


@router.get("/quotes/new")
def quote_new(request: Request):
    return templates.TemplateResponse(
        request, "quote_builder.html", {"active": "quotes", "quote_id": None}
    )


@router.get("/quotes/{quote_id}")
def quote_detail(request: Request, quote_id: int):
    return templates.TemplateResponse(
        request, "quote_builder.html", {"active": "quotes", "quote_id": quote_id}
    )


@router.get("/clients")
def clients(request: Request):
    return templates.TemplateResponse(request, "clients.html", {
        "active": "clients",
        "google_contacts_enabled": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
    })


@router.get("/catalog")
def catalog(request: Request):
    return templates.TemplateResponse(request, "catalog.html", {"active": "catalog"})


@router.get("/settings")
def settings(request: Request):
    return templates.TemplateResponse(request, "settings.html", {"active": "settings"})


@router.get("/admin")
def admin(request: Request):
    """Platform admin console. The page itself is just a shell; every data
    call goes to /api/admin/* which is server-gated by get_current_admin
    (403 for non-admins). admin.html also self-redirects non-admins away."""
    return templates.TemplateResponse(request, "admin.html", {"active": "admin"})
