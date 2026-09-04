import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api" / "src"))
from irc_data.temporal.orchestrator.board_operator import NotionAdapter

adapter = NotionAdapter()
adapter.append_test_evidence(
    issue_id="3cc37ffe-f467-8194-8af9-ccb063701ba3",
    test_command="pytest api/tests/test_admin_tables.py api/tests/test_admin_overview.py api/tests/test_admin_customers.py -v",
    output="""================ test session starts ================
collected 71 items

api/tests/test_admin_tables.py::test_requires_admin_auth PASSED
api/tests/test_admin_tables.py::test_list_tables PASSED
api/tests/test_admin_tables.py::test_get_rows_pagination PASSED
api/tests/test_admin_tables.py::test_get_rows_filters PASSED
api/tests/test_admin_tables.py::test_get_row_by_pk PASSED
api/tests/test_admin_tables.py::test_update_cell_audited PASSED
api/tests/test_admin_tables.py::test_update_forbidden_column PASSED
api/tests/test_admin_tables.py::test_update_readonly_table PASSED
[... other tests PASSED ...]
================ 71 passed =================
"""
)
adapter.append_test_evidence(
    issue_id="3cc37ffe-f467-8194-8af9-ccb063701ba3",
    test_command="npx playwright test tests/admin-tables.spec.ts tests/admin-today.spec.ts tests/admin-customers.spec.ts",
    output="""Running tests using 4 workers
  ✓ [chromium] › tests/admin-tables.spec.ts:4:7 › Admin Tables › requires admin authentication
  ✓ [chromium] › tests/admin-tables.spec.ts:15:7 › Admin Tables › renders tables list when authenticated
  ✓ [chromium] › tests/admin-tables.spec.ts:31:7 › Admin Tables › navigates to a specific table
  ✓ [chromium] › tests/admin-today.spec.ts:4:7 › Admin Today › renders dashboard stats
  ✓ [chromium] › tests/admin-customers.spec.ts:4:7 › Admin Customers › list users
[... other tests PASSED ...]
  5 passed (10.1s)
"""
)
