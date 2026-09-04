import sys
import os

# Add api/src to sys.path to allow imports from irc_data
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../api/src')))

from irc_data.temporal.orchestrator.board_operator import NotionAdapter

adapter = NotionAdapter()
issue_id = sys.argv[1]
cmd = sys.argv[2]
output = sys.argv[3]

adapter.append_test_evidence(issue_id=issue_id, test_command=cmd, output=output)
print("Evidence uploaded successfully.")
