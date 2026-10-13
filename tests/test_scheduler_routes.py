"""T070: the `/` route must SHOW what is scheduled, and let it be cancelled.

The card is the only way to cancel without speaking, so it is tested as a
route contract rather than as markup: a browser-side change that keeps the
endpoints honest is fine, a backend change that quietly renames one is not.
"""

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.scheduler import SchedulerRunner, ScheduleStore  # noqa: E402
from src.scheduler import runner as runner_module  # noqa: E402
from tests.helpers_ui_auth import HTML_COMMENT, make_app_with_user  # noqa: E402


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """A signed-in client and a scheduler holding one known task."""
    from src.api import ui_auth

    store = ScheduleStore(str(tmp_path / "schedules.json"))
    fire_at = (datetime.now() + timedelta(hours=3)).replace(
        minute=0, second=0, microsecond=0)
    task = store.add("liga a luz do balcao", fire_at, source="voice")
    runner = SchedulerRunner(store, executor=lambda a: "ok",
                             speaker=lambda line: None)
    runner_module.register_shared_runner(runner)

    monkeypatch.setattr(ui_auth, "is_authenticated", lambda: True,
                        raising=False)
    app, client = make_app_with_user("b@t.test", "admin", monkeypatch=monkeypatch)
    try:
        yield client, store, task
    finally:
        runner_module.register_shared_runner(None)


class TestScheduleEndpoints:
    def test_lists_what_is_pending(self, wired):
        client, store, task = wired
        data = client.get("/api/schedules").get_json()
        assert [t["id"] for t in data["schedules"]] == [task.id]
        assert data["schedules"][0]["action"] == "liga a luz do balcao"
        assert data["schedules"][0]["label"] == task.when().strftime("%H:%M")

    def test_cancel_removes_it(self, wired):
        client, store, task = wired
        response = client.post(f"/api/schedules/{task.id}/cancel")
        assert response.status_code == 200
        assert response.get_json()["schedules"] == []
        assert store.pending() == []

    def test_cancelling_a_task_that_is_already_gone_says_so(self, wired):
        """404, not a cheerful 200.

        The button was pressed against a card the page drew earlier. If the
        task already fired or was cancelled from the phone, a 200 that removed
        nothing tells the owner the cancellation worked when it did not.
        """
        client, _store, task = wired
        assert client.post(f"/api/schedules/{task.id}/cancel").status_code == 200
        second = client.post(f"/api/schedules/{task.id}/cancel")
        assert second.status_code == 404

    def test_an_unknown_id_is_not_found(self, wired):
        client, _store, _task = wired
        assert client.post("/api/schedules/nope/cancel").status_code == 404

    def test_the_endpoints_require_a_session(self, monkeypatch, tmp_path):
        """These sit on the same page as the light switches."""
        from src.api import ui_auth

        store = ScheduleStore(str(tmp_path / "s.json"))
        runner_module.register_shared_runner(
            SchedulerRunner(store, executor=lambda a: "ok"))
        monkeypatch.setattr(ui_auth, "is_authenticated", lambda: False,
                            raising=False)
        app, client = make_app_with_user("b@t.test", "admin",
                                         monkeypatch=monkeypatch)
        try:
            assert client.get("/api/schedules").status_code == 401
            assert client.post("/api/schedules/x/cancel").status_code == 401
        finally:
            runner_module.register_shared_runner(None)

    def test_no_post_to_create(self, wired):
        """Deliberate omission, asserted so nobody "helpfully" adds a form.

        The owner creates tasks by speaking. A web form would be a third
        expression of the same sentence with its own parsing and its own idea
        of what "as 8" means at 21:00.
        """
        client, _store, _task = wired
        assert client.post("/api/schedules", json={"action": "x"}).status_code in (
            404, 405)


class TestTheCard:
    def test_the_page_has_the_card(self, wired):
        client, _store, _task = wired
        page = HTML_COMMENT.sub("", client.get("/").get_data(as_text=True))
        assert 'id="schedules-card"' in page
        assert 'id="schedules-list"' in page

    def test_the_card_starts_hidden(self, wired):
        """It is unhidden by the fetch. Before /api/schedules answers, an
        empty card reading "Nada agendado" would be a claim, not a fact."""
        client, _store, _task = wired
        page = HTML_COMMENT.sub("", client.get("/").get_data(as_text=True))
        card = page.split('id="schedules-card"', 1)[1][:120]
        assert "hidden" in card

    def test_it_asks_for_the_list_on_load(self, wired):
        client, _store, _task = wired
        page = client.get("/").get_data(as_text=True)
        assert "/api/schedules" in page
        assert "loadSchedules()" in page

    def test_the_action_is_never_interpolated_as_markup(self, wired):
        """The action text is whatever the owner said. innerHTML here would
        make the page execute it."""
        client, _store, _task = wired
        page = client.get("/").get_data(as_text=True)
        body = page.split("function renderSchedules", 1)[1].split(
            "async function loadSchedules", 1)[0]
        assert "innerHTML" not in body
        assert "textContent" in body
