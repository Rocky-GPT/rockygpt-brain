"""Keep tests from loading a developer's .env.

rockygpt-brain/.env can be a 1Password mount, and opening it waits for an
unlock prompt. CI has no .env, so this also makes local runs match CI.
"""

import os

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
