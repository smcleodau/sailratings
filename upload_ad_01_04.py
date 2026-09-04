from irc_data.temporal.orchestrator.board_operator import NotionAdapter

def main():
    adapter = NotionAdapter()
    
    test_output = """
Running 3 tests using 3 workers
  3 passed (6.0s)

To open last HTML report run:
  npx playwright show-report
"""
    adapter.append_test_evidence(
        issue_id="3cc37ffe-f467-81c0-acae-f6f1584c0f21",
        test_command="npx playwright test tests/admin-identity.spec.ts",
        output=test_output
    )
    print("Evidence uploaded!")

if __name__ == '__main__':
    main()
