"""Make the repo root importable, once, for every test module.

Each test file used to insert this path itself, which works until a new file
forgets. One did, and it passed locally and failed in CI on all three Python
versions, because `python -m pytest` puts the working directory on `sys.path`
and the bare `pytest` that CI runs does not. A file that has to remember
something is a file that will eventually not.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
