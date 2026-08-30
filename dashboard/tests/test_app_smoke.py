"""Streamlit shell smoke tests.

Runs the real ``dashboard/app.py`` headlessly through Streamlit's AppTest harness with
every prerequisite pointed at an empty temporary directory. This is the direct check
that the shell renders -- rather than raises -- when there is no factory.json, no
database, no completed runs, no prediction files and no running runtime.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit", reason="dashboard/requirements.txt not installed")

from streamlit.testing.v1 import AppTest  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP = PROJECT_ROOT / "dashboard" / "app.py"


def _launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **env: str) -> AppTest:
    monkeypatch.setenv("DT_DASHBOARD_FACTORY", str(tmp_path / "config" / "factory.json"))
    monkeypatch.setenv("DT_DASHBOARD_DB", str(tmp_path / "db" / "dashboard.db"))
    monkeypatch.setenv("DT_DASHBOARD_RUNS", str(tmp_path / "runs"))
    monkeypatch.setenv("DT_DASHBOARD_GENERATED", str(tmp_path / "generated"))
    monkeypatch.setenv("DT_DASHBOARD_PREDICTIONS", str(tmp_path / "runtime_output"))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    app = AppTest.from_file(str(APP), default_timeout=60)
    app.run()
    return app


def _text(app: AppTest) -> str:
    parts = []
    for collection in (app.markdown, app.caption, app.info, app.warning, app.error, app.title):
        parts.extend(element.value for element in collection)
    for element in app.sidebar.markdown:
        parts.append(element.value)
    return "\n".join(str(part) for part in parts)


class TestColdStart:
    def test_renders_with_nothing_in_place(self, tmp_path: Path, monkeypatch):
        app = _launch(tmp_path, monkeypatch)
        assert not app.exception, [str(e) for e in app.exception]

    def test_shows_the_product_name_and_navigation(self, tmp_path: Path, monkeypatch):
        app = _launch(tmp_path, monkeypatch)
        assert any("DIGITALTWIN.AI" in str(t.value) for t in app.title)
        pages = app.sidebar.radio[0]
        assert "Live Factory" in pages.options
        assert "Run Factory" in pages.options

    def test_offers_the_product_navigation(self, tmp_path: Path, monkeypatch):
        app = _launch(tmp_path, monkeypatch)
        pages = list(app.sidebar.radio[0].options)
        for expected in (
            "Live Factory",
            "Overview",
            "Bottlenecks",
            "Defects",
            "Sensors",
            "Run History",
            "Business Case",
            "Runtime Health",
            "Configuration",
            "Run Factory",
        ):
            assert expected in pages
        assert "What-If" not in pages
        assert "Supervisor" not in pages

    def test_reports_a_missing_factory_without_crashing(self, tmp_path: Path, monkeypatch):
        app = _launch(tmp_path, monkeypatch, DT_DASHBOARD_ALLOW_DEMO_FACTORY="false")
        assert not app.exception, [str(e) for e in app.exception]
        assert "MISSING" in _text(app)

    def test_reports_an_invalid_factory_without_crashing(self, tmp_path: Path, monkeypatch):
        factory = tmp_path / "config" / "factory.json"
        factory.parent.mkdir(parents=True)
        factory.write_text('{"stations": []}', encoding="utf-8")
        app = _launch(tmp_path, monkeypatch)
        assert not app.exception, [str(e) for e in app.exception]
        assert "INVALID" in _text(app)

    def test_generated_demo_factory_is_labelled(self, tmp_path: Path, monkeypatch):
        app = _launch(tmp_path, monkeypatch)
        assert not app.exception
        assert "illustrative" in _text(app).lower()


class TestNoExecutionOnLoad:
    def test_page_load_starts_no_run(self, tmp_path: Path, monkeypatch):
        app = _launch(tmp_path, monkeypatch)
        assert not app.exception
        assert not (tmp_path / "runs").exists()
        assert not (tmp_path / "generated").exists()
        assert not (tmp_path / "runtime_output").exists()

    def test_run_factory_is_isolated_from_the_initial_page(self, tmp_path: Path, monkeypatch):
        app = _launch(tmp_path, monkeypatch)
        labels = [button.label for button in app.button]
        assert not any("RUN FACTORY" in label for label in labels)
        assert not (tmp_path / "runs").exists()


class TestRunHistoryView:
    def test_empty_history_states_so_explicitly(self, tmp_path: Path, monkeypatch):
        app = _launch(tmp_path, monkeypatch)
        app.sidebar.radio[0].set_value("Run History").run()
        assert not app.exception, [str(e) for e in app.exception]
        assert "No completed production runs yet." in _text(app)

    def test_sensor_coverage_renders_from_the_factory(self, tmp_path: Path, monkeypatch):
        app = _launch(tmp_path, monkeypatch)
        app.sidebar.radio[0].set_value("Sensors").run()
        assert not app.exception, [str(e) for e in app.exception]

    @pytest.mark.parametrize(
        "page", ["Live Factory", "Bottlenecks", "Defects", "Sensors", "Overview", "Business Case"]
    )
    def test_placeholder_pages_render(self, tmp_path: Path, monkeypatch, page: str):
        app = _launch(tmp_path, monkeypatch)
        app.sidebar.radio[0].set_value(page).run()
        assert not app.exception, [str(e) for e in app.exception]


class TestRunFactoryPage:
    """Run controls are available only on the dedicated page and stay idle on load."""

    def _open_run_factory(self, app: AppTest) -> AppTest:
        return app.sidebar.radio[0].set_value("Run Factory").run()

    def test_page_shows_execution_controls_or_a_preflight_blocker(self, tmp_path: Path, monkeypatch):
        app = self._open_run_factory(_launch(tmp_path, monkeypatch))
        assert not app.exception, [str(e) for e in app.exception]
        labels = [button.label for button in app.button]
        assert any("RUN FACTORY" in label for label in labels)

    def test_page_load_starts_no_run(self, tmp_path: Path, monkeypatch):
        app = self._open_run_factory(_launch(tmp_path, monkeypatch))
        assert not app.exception
        assert not (tmp_path / "runs").exists()
        assert not (tmp_path / "generated").exists()
