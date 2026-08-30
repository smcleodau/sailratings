"""pytest config for the sources test package."""

import asyncio
import sys

# Python 3.12 on this host: ``asyncio.run`` needs no special policy.
# pytest-asyncio is available but these tests use explicit ``asyncio.run``
# so they don't need the asyncio mode flag.
