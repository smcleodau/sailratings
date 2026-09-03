from src.irc_data.temporal.orchestrator.board_operator import NotionAdapter

adapter = NotionAdapter()
output = """============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.2, pluggy-1.6.0 -- /usr/bin/python3
cachedir: .pytest_cache
rootdir: /home/irc-data/code/sailratings/worktrees/3cc37ffe-f467-81b2-968f-d4dc7ee9e877/api
configfile: pyproject.toml
plugins: asyncio-1.3.0, libtmux-0.62.0, timeout-2.4.0, anyio-4.9.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 10 items

tests/matching/test_adjudication_api.py::test_requires_admin_auth PASSED
tests/matching/test_adjudication_api.py::test_enqueue_uncertain_candidate_and_read_queue PASSED
tests/matching/test_adjudication_api.py::test_enqueue_confident_candidate_stays_with_auto_resolver PASSED
tests/matching/test_adjudication_api.py::test_queue_is_prioritised_high_impact_first PASSED
tests/matching/test_adjudication_api.py::test_decide_applies_through_shared_contract PASSED
tests/matching/test_adjudication_api.py::test_double_review_enforced_over_http PASSED
tests/matching/test_adjudication_api.py::test_reverse_requeues_case_over_http PASSED
tests/matching/test_adjudication_api.py::test_case_detail_includes_resolution_trail PASSED
tests/matching/test_adjudication_api.py::test_unknown_case_404 PASSED
tests/matching/test_adjudication_api.py::test_invalid_candidate_422 PASSED

======================= 10 passed, 2 warnings in 1.52s ========================"""

adapter.append_test_evidence(issue_id="3cc37ffef46781b2968fd4dc7ee9e877", test_command="python3 -m pytest tests/matching/test_adjudication_api.py -v", output=output)
print("Evidence posted successfully.")
