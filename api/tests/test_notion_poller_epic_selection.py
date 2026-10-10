from irc_data.temporal.orchestrator.notion_poller import pick_active_epic


def _page(id_, epic=None, blocked_by="", gated=False):
    props = {
        "ID": {"type": "rich_text", "rich_text": [{"text": {"content": id_}, "plain_text": id_}]},
        "Blocked By": {"type": "rich_text", "rich_text": [{"text": {"content": blocked_by}, "plain_text": blocked_by}] if blocked_by else []},
        "Parent Epic": {"type": "rich_text", "rich_text": [{"text": {"content": epic}, "plain_text": epic}] if epic else []},
        "Human Gate": {"type": "checkbox", "checkbox": gated},
    }
    return {"id": id_.lower(), "properties": props}


def _gated(page):
    return page["properties"]["Human Gate"]["checkbox"]


def test_skips_epic_whose_ungated_tasks_are_all_blocked():
    epics = [_page("AI-01"), _page("IN-01")]
    tasks = [
        _page("AI-01-02", "AI-01", gated=True),
        _page("AI-01-03", "AI-01", blocked_by="AI-01-02"),
        _page("IN-01-01", "IN-01", blocked_by="DP-06-04"),
    ]
    epic_id, dispatchable = pick_active_epic(epics, tasks, done_ids={"DP-06-04"}, is_gated=_gated)
    assert epic_id == "IN-01"
    assert [t["id"] for t in dispatchable] == ["in-01-01"]


def test_skips_epic_with_only_gated_tasks():
    epics = [_page("AD-01"), _page("IN-01")]
    tasks = [_page("AD-01-07", "AD-01", gated=True), _page("IN-01-01", "IN-01")]
    epic_id, dispatchable = pick_active_epic(epics, tasks, done_ids=set(), is_gated=_gated)
    assert epic_id == "IN-01"


def test_first_epic_with_dispatchable_work_wins_and_order_is_by_id():
    epics = [_page("AI-01"), _page("IN-01")]
    tasks = [
        _page("AI-01-05", "AI-01"),
        _page("AI-01-03", "AI-01"),
        _page("AI-01-07", "AI-01", blocked_by="AI-01-04"),
        _page("IN-01-01", "IN-01"),
    ]
    epic_id, dispatchable = pick_active_epic(epics, tasks, done_ids=set(), is_gated=_gated)
    assert epic_id == "AI-01"
    assert [t["id"] for t in dispatchable] == ["ai-01-03", "ai-01-05"]


def test_nothing_dispatchable_anywhere():
    epics = [_page("AI-01")]
    tasks = [_page("AI-01-03", "AI-01", blocked_by="AI-01-02")]
    assert pick_active_epic(epics, tasks, done_ids=set(), is_gated=_gated) == (None, [])
