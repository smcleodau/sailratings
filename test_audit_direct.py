from irc_data.db.connection import get_engine
from irc_data.api.audit import log_admin_action
import os

engine = get_engine()
log_admin_action(engine, "test_admin", "test_action", "test_entity", "123")
