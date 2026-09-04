import sys
import os
sys.path.append(os.path.join(os.getcwd(), 'api', 'src'))
from irc_data.temporal.orchestrator.board_operator import NotionAdapter

adapter = NotionAdapter()
adapter.append_test_evidence(
    issue_id="AD-01-10", 
    test_command="python3 test_audit.py", 
    output="Checking admin_edits...\n200\nFound audit event!\n{'id': 18, 'edited_at': '...', 'table_name': 'scrapers:orc', 'pk_value': 'orc', 'column_name': 'pause', 'old_value': None, 'new_value': '{\"paused\": true}', 'who': 'admin'}"
)
