# =============================================================================
# File:     app.py
# Author:   Elijah Samuel
# Course:   CS 499 Computer Science Capstone, SNHU
# Date:     2026-10-11
# Purpose:  Grazioso Salvare rescue dashboard as a plain Dash app (replaces
#           the CS 340 jupyter_dash notebook). Every database call goes
#           through shelter.AnimalShelter; the browser only ever sends a
#           profile key and a page number.
# =============================================================================
"""Run with:  python app.py   (needs MONGO_URI; see .env.example)

Fixes from the CS 340 code review, carried into this port:
  * broken import (`from AnimalShelter import ...`) -> `from shelter import AnimalShelter`
  * missing logo file -> a local SVG in assets/, served by Dash
  * the "None"/reset case is handled first, not last
  * map callback checks for an empty table and an out-of-range row
  * no FIX ME leftovers; no full-collection load into pandas at startup
  * the table pages on the server (page_action="custom")
"""

from __future__ import annotations

import atexit
import logging
import math
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import dash_leaflet as dl
import plotly.express as px
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, ctx, dash_table, dcc, html
from dash.dash_table.Format import Format, Scheme
from dotenv import load_dotenv

from shelter import (
    ALL_ANIMALS,
    PROFILE_DEFINITIONS,
    AnimalShelter,
    ConfigurationError,
    ShelterError,
)
from shelter.animal_shelter import MAX_PAGE_SIZE

logger = logging.getLogger("dashboard")

PAGE_SIZE = 10
TOP_BREEDS = 10
AUSTIN = (30.2672, -97.7431)  # fallback map center only; never shown as an animal

COLUMNS = [
    {"name": "Animal ID", "id": "animal_id"},
    {"name": "Name", "id": "name"},
    {"name": "Type", "id": "animal_type"},
    {"name": "Breed", "id": "breed"},
    {"name": "Color", "id": "color"},
    {"name": "Sex", "id": "sex_upon_outcome"},
    {"name": "Age", "id": "age_upon_outcome"},
    {"name": "Age (weeks)", "id": "age_upon_outcome_in_weeks", "type": "numeric",
     "format": Format(precision=1, scheme=Scheme.fixed)},
    {"name": "Outcome", "id": "outcome_type"},
    {"name": "Lat", "id": "location_lat", "type": "numeric", "format": Format(precision=4, scheme=Scheme.fixed)},
    {"name": "Long", "id": "location_long", "type": "numeric", "format": Format(precision=4, scheme=Scheme.fixed)},
]

PROFILE_OPTIONS = [{"label": "All animals (reset)", "value": ALL_ANIMALS}] + [
    {"label": p.label, "value": key} for key, p in PROFILE_DEFINITIONS.items()
]

FRIENDLY_ERROR = "Something went wrong loading data. Please try again."


def normalize_profile(value: Any) -> str:
    """None and the reset option are checked FIRST (code-review fix)."""
    if value is None or value == ALL_ANIMALS:
        return ALL_ANIMALS
    return value  # anything else is checked against the whitelist by the shelter layer


def user_message(exc: ShelterError) -> str:
    """Typed errors already carry safe text. Anything unexpected gets a generic line."""
    return str(exc) if isinstance(exc, ShelterError) else FRIENDLY_ERROR


# ---------------------------------------------------------------------------
# Callback bodies as plain functions (unit-testable with a mongomock shelter)
# ---------------------------------------------------------------------------

def table_page(shelter: AnimalShelter, profile: Any, page: Any, page_size: Any) -> Tuple[List[dict], int, str]:
    """Return (rows, page_count, status_message) for one page of the table."""
    profile = normalize_profile(profile)
    page = page if isinstance(page, int) and not isinstance(page, bool) and page >= 0 else 0
    page_size = page_size if isinstance(page_size, int) and 1 <= page_size <= MAX_PAGE_SIZE else PAGE_SIZE
    try:
        total = shelter.count_by_profile(profile)
        page_count = max(1, math.ceil(total / page_size))
        page = min(page, page_count - 1)
        rows = shelter.query_by_profile(profile, page=page, page_size=page_size)
        return rows, page_count, ""
    except ShelterError as exc:
        logger.warning("table load failed: %s", type(exc).__name__)
        return [], 1, user_message(exc)


def criteria_text(profile: Any) -> str:
    profile = normalize_profile(profile)
    if profile == ALL_ANIMALS:
        return "Showing every animal in the shelter data, 10 per page, youngest first."
    p = PROFILE_DEFINITIONS.get(profile)
    if p is None:
        return ""
    return (f"{p.label}: {', '.join(p.breeds)} · {p.sex} · "
            f"{p.min_weeks:g}–{p.max_weeks:g} weeks old (inclusive)")


def breed_figure(shelter: AnimalShelter, profile: Any) -> Tuple[go.Figure, str]:
    """Bar chart of breed counts from a $group pipeline (counting done by MongoDB)."""
    profile = normalize_profile(profile)
    try:
        total = shelter.count_by_profile(profile)
        counts = shelter.breed_counts(profile, top=TOP_BREEDS)
    except ShelterError as exc:
        logger.warning("chart load failed: %s", type(exc).__name__)
        return _empty_figure("Breed chart unavailable"), user_message(exc)
    if not counts:
        return _empty_figure("No animals match this filter"), ""
    shown = sum(c["count"] for c in counts)
    title = f"Breeds — {total:,} animal{'s' if total != 1 else ''}"
    if shown < total:
        title += f" (top {len(counts)} breeds shown, {total - shown:,} in other breeds)"
    ordered = list(reversed(counts))  # largest bar on top
    fig = px.bar(
        x=[c["count"] for c in ordered],
        y=[c["breed"] for c in ordered],
        orientation="h",
        labels={"x": "Animals", "y": ""},
        title=title,
        text=[c["count"] for c in ordered],
    )
    fig.update_traces(marker_color="#1f5f8b", textposition="outside", cliponaxis=False)
    # Height follows the number of bars so one breed is not drawn as a giant block.
    fig.update_layout(margin=dict(l=10, r=30, t=50, b=30), height=110 + 34 * len(counts), bargap=0.3,
                      plot_bgcolor="#fff", title_font_size=15, xaxis=dict(gridcolor="#eef1f4"))
    return fig, ""


def _empty_figure(message: str) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=message, showarrow=False, font=dict(size=15, color="#5b6876"))
    fig.update_layout(xaxis_visible=False, yaxis_visible=False, height=420, plot_bgcolor="#fff",
                      margin=dict(l=10, r=10, t=30, b=10))
    return fig


def _has_coordinates(row: Dict[str, Any]) -> bool:
    lat, lon = row.get("location_lat"), row.get("location_long")
    return (isinstance(lat, (int, float)) and isinstance(lon, (int, float))
            and math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180)


def map_children(rows: Optional[List[dict]], selected: Optional[List[int]]) -> List[Any]:
    """Map centered on the selected row. Every guard the CS 340 version lacked."""
    if not rows:
        return [html.Div("No animals match this filter, so there is nothing to map.", className="empty")]
    index = selected[0] if selected and isinstance(selected[0], int) else 0
    if not 0 <= index < len(rows):
        index = 0
    row = rows[index]
    if not _has_coordinates(row):
        return [html.Div("This animal has no location data to map.", className="empty")]

    lat, lon = row["location_lat"], row["location_long"]
    others = [
        dl.CircleMarker(center=[r["location_lat"], r["location_long"]], radius=5, color="#c0612b",
                        fillOpacity=0.6, children=[dl.Tooltip(f"{r.get('animal_id')} · {r.get('breed')}")])
        for i, r in enumerate(rows) if i != index and _has_coordinates(r)
    ]
    selected_marker = dl.Marker(
        position=[lat, lon],
        children=[
            dl.Tooltip(row.get("breed", "")),
            dl.Popup([html.H4(row.get("name") or "(no name)"), html.P(row.get("animal_id", ""))]),
        ],
    )
    return [
        html.Div(f"Selected: {row.get('name') or '(no name)'} · {row.get('animal_id')} · {row.get('breed')}",
                 style={"marginBottom": "6px", "fontWeight": 600}),
        dl.Map(
            id="leaflet-map",
            center=[lat, lon],
            zoom=11,
            style={"width": "100%", "height": "390px"},
            children=[dl.TileLayer(id="base-layer-id"), *others, selected_marker],
        ),
        html.Div("Orange dots: other animals on this page.", style={"color": "#5b6876", "fontSize": "0.85rem"}),
    ]


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def signout_area(user: Optional[Dict[str, str]]) -> List[Any]:
    """Shown only when the employer portal is on and someone is signed in."""
    if not user:
        return []
    return [
        html.Span(f"Signed in as {user['name']} ({user['company']}) "),
        html.Form(action="/portal/signout", method="POST", style={"display": "inline"},
                  children=[html.Button("Sign out", type="submit")]),
    ]


def build_layout(user: Optional[Dict[str, str]] = None) -> html.Div:
    return html.Div(className="page", children=[
        html.Div(className="header", children=[
            html.Img(src="/assets/grazioso_salvare_logo.svg", alt="Grazioso Salvare logo"),
            html.Div([
                html.H1("Grazioso Salvare Rescue Dashboard"),
                html.P("Elijah Samuel · CS 499 enhancement of the CS 340 Austin Animal Center dashboard"),
            ]),
            html.Div(id="signout-slot", className="signout", children=signout_area(user)),
        ]),
        html.Div(className="controls", children=[
            dcc.RadioItems(id="filter-type", options=PROFILE_OPTIONS, value=ALL_ANIMALS, inline=True),
        ]),
        html.Div(id="criteria", className="criteria"),
        html.Div(id="table-status", className="status"),
        html.Div(className="panel", children=[
            dash_table.DataTable(
                id="datatable-id",
                columns=COLUMNS,
                data=[],
                page_action="custom",      # the server sends one page at a time
                page_current=0,
                page_size=PAGE_SIZE,
                page_count=1,
                row_selectable="single",
                selected_rows=[0],
                style_table={"overflowX": "auto"},
                style_cell={"textAlign": "left", "padding": "6px", "fontSize": "0.9rem", "fontFamily": "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"},
                style_header={"backgroundColor": "#eef1f4", "fontWeight": "bold"},
                style_data_conditional=[{"if": {"state": "selected"}, "backgroundColor": "#d2e6f5"}],
            ),
        ]),
        html.Div(id="chart-status", className="status"),
        html.Div(className="row", children=[
            html.Div(className="panel", children=[dcc.Graph(id="breed-chart", config={"displayModeBar": False})]),
            html.Div(className="panel", id="map-id"),
        ]),
    ])


def create_app(shelter: AnimalShelter, portal=None) -> Dash:
    app = Dash(__name__, title="Grazioso Salvare Dashboard", assets_folder="assets")
    if portal is None:
        app.layout = build_layout()
    else:
        portal.install(app)
        # A function layout is built per page load, so it can show who is signed in.
        app.layout = lambda: build_layout(portal.current_user())

    @app.callback(
        Output("datatable-id", "data"),
        Output("datatable-id", "page_count"),
        Output("datatable-id", "page_current"),
        Output("datatable-id", "selected_rows"),
        Output("table-status", "children"),
        Output("criteria", "children"),
        Input("filter-type", "value"),
        Input("datatable-id", "page_current"),
        State("datatable-id", "page_size"),
    )
    def update_table(profile, page_current, page_size):
        # A new filter always starts at page 0.
        page = 0 if ctx.triggered_id == "filter-type" else page_current
        rows, page_count, status = table_page(shelter, profile, page, page_size)
        page = min(page or 0, page_count - 1)
        return rows, page_count, page, [0] if rows else [], status, criteria_text(profile)

    @app.callback(
        Output("breed-chart", "figure"),
        Output("chart-status", "children"),
        Input("filter-type", "value"),
    )
    def update_chart(profile):
        return breed_figure(shelter, profile)

    @app.callback(
        Output("map-id", "children"),
        Input("datatable-id", "data"),
        Input("datatable-id", "selected_rows"),
    )
    def update_map(rows, selected):
        return map_children(rows, selected)

    return app


def main() -> int:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        shelter = AnimalShelter.from_env()
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    atexit.register(shelter.close)
    try:
        shelter.ping()
        logger.info("connected to %s", shelter.settings)
    except ShelterError as exc:
        logger.warning("database not reachable at startup (%s); the page will show an error until it is", exc)

    portal = None
    if os.environ.get("PORTAL_ENABLED", "0") == "1":
        from shelter.portal import EmployerPortal
        portal = EmployerPortal.from_env(shelter)

    app = create_app(shelter, portal)
    host = os.environ.get("DASH_HOST", "127.0.0.1")
    port = int(os.environ.get("DASH_PORT", "8050"))
    # Debug mode shows tracebacks in the browser, so it is off unless asked for.
    app.run(host=host, port=port, debug=os.environ.get("DASH_DEBUG") == "1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
