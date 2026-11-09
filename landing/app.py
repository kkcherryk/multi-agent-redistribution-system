"""Role-selection landing page (port 8080): choose Restaurant, NGO or Host before login (Module 1)."""
from __future__ import annotations

import html

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

CARDS = [
    ("restaurant", "Restaurant", "Register your restaurant and share surplus food with nearby NGOs."),
    ("ngo", "NGO", "Collect surplus food and deliver it to orphanages, shelters and old-age homes."),
    ("admin", "Host", "Project team: review applications and oversee food movement."),
]


def create_landing_app(portal_urls: dict[str, str]) -> FastAPI:
    app = FastAPI(title="FRS landing")
    cards = "".join(
        f'<a class="card card-{key}" href="{html.escape(portal_urls[key])}" data-portal="{key}">'
        f"<h2>{html.escape(name)}</h2><p>{html.escape(desc)}</p><span>Continue &rarr;</span></a>"
        for key, name, desc in CARDS
    )
    page = PAGE.replace("{{CARDS}}", cards)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return page

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "landing"}

    return app


def build_default_app() -> FastAPI:
    from shared.config import get_settings

    return create_landing_app(get_settings().portal_urls)


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Food Redistribution System</title>
<style>
:root{--bg:#f6f7f4;--fg:#1d2420;--muted:#5d675f;--card:#fff;--line:#dfe3dc;--rest:#1f6feb;--ngo:#1a7f37;--host:#6e40c9}
@media (prefers-color-scheme:dark){:root{--bg:#121513;--fg:#e8ece9;--muted:#9aa49d;--card:#1b201d;--line:#2c332e}}
*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--bg);color:var(--fg)}
main{max-width:960px;margin:0 auto;padding:56px 16px}
h1{font-size:2rem;margin:0 0 8px}.lead{color:var(--muted);margin:0 0 32px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px}
.card{display:block;padding:24px;background:var(--card);border:1px solid var(--line);border-top:4px solid;border-radius:10px;color:inherit;text-decoration:none}
.card:hover{transform:translateY(-2px);box-shadow:0 6px 20px rgba(0,0,0,.08)}
.card h2{margin:0 0 8px}.card p{color:var(--muted);margin:0 0 16px;line-height:1.45}.card span{font-weight:600}
.card-restaurant{border-top-color:var(--rest)}.card-ngo{border-top-color:var(--ngo)}.card-admin{border-top-color:var(--host)}
</style></head><body><main>
<h1>Multi-Agent Food Redistribution System</h1>
<p class="lead">Choose how you are signing in. Each role has its own portal.</p>
<div class="grid">{{CARDS}}</div>
</main></body></html>"""
