from irc_data.temporal.orchestrator.board_operator import NotionAdapter

adapter = NotionAdapter()
output = """Running 79 tests using 16 workers
  73 passed (12.6s)
  6 skipped
"""

adapter.append_test_evidence(issue_id="3cc37ffef46781b2968fd4dc7ee9e877", test_command="npx playwright test", output=output)
print("Evidence posted successfully.")
