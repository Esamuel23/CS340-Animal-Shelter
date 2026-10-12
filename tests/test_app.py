# =============================================================================
# File:     test_app.py
# Author:   Elijah Samuel
# Course:   CS 499 Computer Science Capstone, SNHU
# Date:     2026-10-11
# Purpose:  Dashboard callback logic on a mongomock shelter: the CS 340
#           code-review fixes (None first, empty-table map, bounds checks)
#           and friendly errors when the database is down.
# =============================================================================
"""Unit tests for app.py helpers and the app factory."""

import app as dashboard
from shelter import DatabaseUnavailable


class DownShelter:
    """Stands in for a shelter whose database is unreachable."""

    def count_by_profile(self, profile):
        raise DatabaseUnavailable("Could not count animals: the database is not reachable right now.")

    query_by_profile = breed_counts = count_by_profile


def test_none_and_reset_are_normalized_first():
    assert dashboard.normalize_profile(None) == "all"
    assert dashboard.normalize_profile("all") == "all"
    assert dashboard.normalize_profile("water") == "water"


def test_table_page_returns_one_page_and_count(mock_shelter):
    rows, page_count, status = dashboard.table_page(mock_shelter, "water", 0, 5)
    assert len(rows) == 5 and page_count == 3 and status == ""


def test_table_page_clamps_page_past_the_end(mock_shelter):
    rows, page_count, _ = dashboard.table_page(mock_shelter, "water", 99, 5)
    assert page_count == 3 and len(rows) == 4   # last page, not an empty one


def test_table_page_rejects_unknown_profile_safely(mock_shelter):
    rows, page_count, status = dashboard.table_page(mock_shelter, "{'$ne': 1}", 0, 10)
    assert rows == [] and page_count == 1 and "Unknown rescue profile" in status


def test_database_down_shows_friendly_message():
    rows, _, status = dashboard.table_page(DownShelter(), "water", 0, 10)
    assert rows == [] and "not reachable" in status and "Traceback" not in status
    fig, chart_status = dashboard.breed_figure(DownShelter(), "water")
    assert "not reachable" in chart_status


def test_map_handles_empty_table():
    out = dashboard.map_children([], [0])
    assert "nothing to map" in str(out)
    assert "nothing to map" in str(dashboard.map_children(None, None))


def test_map_out_of_range_selection_falls_back_to_first_row(mock_shelter):
    rows = mock_shelter.query_by_profile("water", 0, 3)
    out = str(dashboard.map_children(rows, [7]))
    assert rows[0]["animal_id"] in out


def test_map_row_without_coordinates():
    out = str(dashboard.map_children([{"animal_id": "T1", "breed": "x"}], [0]))
    assert "no location data" in out


def test_breed_chart_counts_come_from_the_database(mock_shelter):
    fig, status = dashboard.breed_figure(mock_shelter, "mountain")
    assert status == ""
    assert sum(fig.data[0].x) == mock_shelter.count_by_profile("mountain")


def test_criteria_text_lists_the_spec(mock_shelter):
    text = dashboard.criteria_text("water")
    assert "Intact Female" in text and "26–156 weeks" in text and "Newfoundland" in text


def test_app_factory_builds_with_server_side_paging(mock_shelter):
    app = dashboard.create_app(mock_shelter)
    table = app.layout.children[4].children[0]
    assert table.page_action == "custom"
    assert app.server is not None
