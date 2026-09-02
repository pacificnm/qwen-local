"""Issues tab: app.repos.issues (pure GitHub REST wrapper, no local clone).

No network: get_json/post_json/patch_json (app.repos.github_api, imported
into app.repos.issues) are monkeypatched — same seam style as
test_gitops_phase4.py's open_pull_request tests.
"""

import pytest

from app.repos import issues
from app.repos.errors import GithubApiError

# --- list_issues ---------------------------------------------------------


async def test_list_issues_filters_out_pull_requests(monkeypatch):
    async def fake_get(url, headers):
        assert "state=open" in url
        return (
            200,
            [
                {"number": 1, "title": "A bug", "user": {"login": "a"}, "labels": [], "assignees": []},
                {"number": 2, "title": "A PR", "pull_request": {"url": "..."}},
            ],
            {},
        )

    monkeypatch.setattr(issues, "get_json", fake_get)
    result = await issues.list_issues("o/r", "pat")
    assert [i["number"] for i in result["items"]] == [1]
    assert result["items"][0]["title"] == "A bug"


async def test_list_issues_has_more_from_link_header(monkeypatch):
    async def fake_get(url, headers):
        return 200, [], {"link": '<https://api.github.com/x?page=2>; rel="next"'}

    monkeypatch.setattr(issues, "get_json", fake_get)
    result = await issues.list_issues("o/r", "pat")
    assert result["has_more"] is True


async def test_list_issues_no_more_without_next_link(monkeypatch):
    async def fake_get(url, headers):
        return 200, [], {"link": '<https://api.github.com/x?page=1>; rel="prev"'}

    monkeypatch.setattr(issues, "get_json", fake_get)
    result = await issues.list_issues("o/r", "pat")
    assert result["has_more"] is False


async def test_list_issues_raises_on_error_status(monkeypatch):
    async def fake_get(url, headers):
        return 404, {"message": "Not Found"}, {}

    monkeypatch.setattr(issues, "get_json", fake_get)
    with pytest.raises(GithubApiError):
        await issues.list_issues("o/r", "pat")


async def test_list_issues_labels_param_included(monkeypatch):
    captured = {}

    async def fake_get(url, headers):
        captured["url"] = url
        return 200, [], {}

    monkeypatch.setattr(issues, "get_json", fake_get)
    await issues.list_issues("o/r", "pat", labels="bug,urgent")
    assert "labels=bug,urgent" in captured["url"]


# --- get_issue / list_comments --------------------------------------------


async def test_get_issue_simplifies_fields(monkeypatch):
    async def fake_get(url, headers):
        assert url.endswith("/issues/42")
        return (
            200,
            {
                "number": 42,
                "title": "Fix login",
                "body": "details",
                "state": "open",
                "user": {"login": "alice"},
                "labels": [{"name": "bug", "color": "ff0000"}],
                "assignees": [{"login": "bob", "avatar_url": "http://x"}],
                "comments": 3,
                "created_at": "t1",
                "updated_at": "t2",
                "html_url": "https://github.com/o/r/issues/42",
            },
            {},
        )

    monkeypatch.setattr(issues, "get_json", fake_get)
    issue = await issues.get_issue("o/r", "pat", 42)
    assert issue["number"] == 42
    assert issue["user"] == "alice"
    assert issue["labels"] == [{"name": "bug", "color": "ff0000"}]
    assert issue["assignees"] == [{"login": "bob", "avatar_url": "http://x"}]


async def test_list_comments_simplifies_fields(monkeypatch):
    async def fake_get(url, headers):
        assert url.endswith("/issues/42/comments")
        return (
            200,
            [{"id": 1, "body": "hi", "user": {"login": "bob", "avatar_url": "u"}, "created_at": "t"}],
            {},
        )

    monkeypatch.setattr(issues, "get_json", fake_get)
    comments = await issues.list_comments("o/r", "pat", 42)
    assert comments == [{"id": 1, "body": "hi", "user": "bob", "avatar_url": "u", "created_at": "t"}]


# --- create_issue / add_comment -------------------------------------------


async def test_create_issue_sends_labels_and_assignees(monkeypatch):
    captured = {}

    async def fake_post(url, headers, payload):
        captured.update(url=url, payload=payload)
        return 201, {"number": 9, "title": "T", "body": "B", "state": "open", "labels": [], "assignees": []}

    monkeypatch.setattr(issues, "post_json", fake_post)
    issue = await issues.create_issue("o/r", "pat", "T", "B", labels=["bug"], assignees=["alice"])
    assert issue["number"] == 9
    assert captured["payload"]["labels"] == ["bug"]
    assert captured["payload"]["assignees"] == ["alice"]


async def test_create_issue_raises_on_non_201(monkeypatch):
    async def fake_post(url, headers, payload):
        return 422, {"message": "Validation failed"}

    monkeypatch.setattr(issues, "post_json", fake_post)
    with pytest.raises(GithubApiError):
        await issues.create_issue("o/r", "pat", "T", "B")


async def test_add_comment_posts_body(monkeypatch):
    captured = {}

    async def fake_post(url, headers, payload):
        captured.update(url=url, payload=payload)
        return 201, {"id": 5, "body": "hello", "user": {"login": "a", "avatar_url": "u"}, "created_at": "t"}

    monkeypatch.setattr(issues, "post_json", fake_post)
    comment = await issues.add_comment("o/r", "pat", 42, "hello")
    assert comment["id"] == 5
    assert captured["url"].endswith("/issues/42/comments")
    assert captured["payload"] == {"body": "hello"}


# --- update_issue: partial payload + full-replace labels/assignees -------


async def test_update_issue_only_sends_provided_fields(monkeypatch):
    captured = {}

    async def fake_patch(url, headers, payload):
        captured.update(url=url, payload=payload)
        return 200, {"number": 42, "title": "New", "body": "", "state": "open", "labels": [], "assignees": []}

    monkeypatch.setattr(issues, "patch_json", fake_patch)
    await issues.update_issue("o/r", "pat", 42, title="New")
    assert captured["payload"] == {"title": "New"}


async def test_update_issue_close_sends_state_only(monkeypatch):
    captured = {}

    async def fake_patch(url, headers, payload):
        captured.update(payload=payload)
        return 200, {"number": 42, "title": "T", "body": "", "state": "closed", "labels": [], "assignees": []}

    monkeypatch.setattr(issues, "patch_json", fake_patch)
    issue = await issues.update_issue("o/r", "pat", 42, state="closed")
    assert captured["payload"] == {"state": "closed"}
    assert issue["state"] == "closed"


async def test_update_issue_labels_and_assignees_are_full_replace(monkeypatch):
    captured = {}

    async def fake_patch(url, headers, payload):
        captured.update(payload=payload)
        return 200, {"number": 42, "title": "T", "body": "", "state": "open", "labels": [], "assignees": []}

    monkeypatch.setattr(issues, "patch_json", fake_patch)
    await issues.update_issue("o/r", "pat", 42, labels=["bug", "p1"], assignees=[])
    assert captured["payload"] == {"labels": ["bug", "p1"], "assignees": []}


async def test_update_issue_raises_on_error(monkeypatch):
    async def fake_patch(url, headers, payload):
        return 404, {"message": "Not Found"}

    monkeypatch.setattr(issues, "patch_json", fake_patch)
    with pytest.raises(GithubApiError):
        await issues.update_issue("o/r", "pat", 999, title="x")


# --- list_labels / list_assignable_users ----------------------------------


async def test_list_labels_simplifies_fields(monkeypatch):
    async def fake_get(url, headers):
        assert url.endswith("/labels")
        return 200, [{"name": "bug", "color": "ff0000", "id": 1, "default": True}], {}

    monkeypatch.setattr(issues, "get_json", fake_get)
    labels = await issues.list_labels("o/r", "pat")
    assert labels == [{"name": "bug", "color": "ff0000"}]


async def test_list_assignable_users_simplifies_fields(monkeypatch):
    async def fake_get(url, headers):
        assert url.endswith("/assignees")
        return 200, [{"login": "alice", "avatar_url": "u", "id": 1}], {}

    monkeypatch.setattr(issues, "get_json", fake_get)
    users = await issues.list_assignable_users("o/r", "pat")
    assert users == [{"login": "alice", "avatar_url": "u"}]
