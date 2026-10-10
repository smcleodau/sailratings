from irc_data.temporal.orchestrator.notion_poller import epics_ready_to_close


def _page(id_, status, epic=None):
    return {
        "id": id_.lower(),
        "properties": {
            "ID": {"type": "rich_text", "rich_text": [{"text": {"content": id_}}]},
            "Status": {"type": "select", "select": {"name": status}},
            "Parent Epic": {"type": "rich_text", "rich_text": [{"text": {"content": epic}}] if epic else []},
        },
    }


def _ids(pages):
    return sorted(p["id"] for p in pages)


def test_closes_epic_when_every_task_is_done():
    pages = [_page("SM-01", "Ready"), _page("SM-01-01", "Done", "SM-01"), _page("SM-01-02", "Done", "SM-01")]
    assert _ids(epics_ready_to_close(pages)) == ["sm-01"]


def test_keeps_epic_open_while_any_task_is_not_done():
    pages = [_page("AD-01", "Ready"), _page("AD-01-01", "Done", "AD-01"), _page("AD-01-18b", "Hold", "AD-01")]
    assert epics_ready_to_close(pages) == []


def test_ignores_epics_with_no_tasks_already_done_or_reference():
    pages = [
        _page("TEMPLATE-01", "Reference"),
        _page("NEW-01", "Ready"),
        _page("DP-01", "Done"), _page("DP-01-01", "Done", "DP-01"),
        _page("REF-01", "Reference"), _page("REF-01-01", "Done", "REF-01"),
    ]
    assert epics_ready_to_close(pages) == []
