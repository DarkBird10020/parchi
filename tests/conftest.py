"""One environment for the test suite, and it is the one CI has.

Two jobs, both of them about the gap between "passes here" and "passes there".

**The repo root on `sys.path`.** Each test file used to insert it itself, which
works until a new file forgets. One did, and it passed locally and failed in CI
on all three Python versions, because `python -m pytest` puts the working
directory on `sys.path` and the bare `pytest` CI runs does not.

**A neutral `.env`.** A developer's `.env` holds an API key, a console
operator and a seed account; CI holds none of them. That difference has broken
this suite twice. A console with an operator configured answers an
unauthenticated request with 401; a console with nothing configured answers
503, so three tests asserting 401 passed locally and failed in CI. Blanking
these here costs nothing, because `load_dotenv` lets a real environment
variable win, so an explicit `PARCHI_... = ...` in the shell still overrides.

The live paths are exercised by the scripts in `eval/`, deliberately, where a
run that needs a key says so instead of quietly scoring something else.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set before any test imports demo.server, which reads all of these at import.
for _name in ("PARCHI_OPENAI_API_KEY", "PARCHI_CONSOLE_TOKEN",
              "PARCHI_CONSOLE_EMAIL", "PARCHI_CONSOLE_PASSWORD_HASH",
              "PARCHI_DEMO_USER_EMAIL", "PARCHI_DEMO_USER_PASSWORD",
              "PARCHI_ALERT_WEBHOOK", "PARCHI_HUMAN_APPROVAL_SECRET",
              "PARCHI_GUARD_MODEL"):
    os.environ.setdefault(_name, "")
