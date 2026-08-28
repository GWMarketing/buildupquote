"""Browser pages for the BuildUpQuote UI (Jinja2 templates).

Pages are thin shells -- the real data is fetched client-side against the
REST API using the JWT stored in localStorage. A small script in base.html
redirects to /login when no token is present.
"""
import os

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

router = APIRouter(include_in_schema=False)

_TEMPLATES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"
)
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


@router.get("/")
def home(request: Request):
    """Serve the dashboard directly on the root URL -- always 200, with no
    307 hop that clients, health checks, or proxies must follow."""
    return templates.TemplateResponse(request, "dashboard.html", {"active": "dashboard"})


@router.get("/login")
def login(request: Request):
    return templates.TemplateResponse(request, "login.html", {"active": ""})


@router.get("/register")
def register(request: Request):
    return templates.TemplateResponse(request, "register.html", {"active": ""})


@router.get("/dashboard")
def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {"active": "dashboard"})


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
    return templates.TemplateResponse(request, "clients.html", {"active": "clients"})


@router.get("/catalog")
def catalog(request: Request):
    return templates.TemplateResponse(request, "catalog.html", {"active": "catalog"})


@router.get("/settings")
def settings(request: Request):
    return templates.TemplateResponse(request, "settings.html", {"active": "settings"})
