# SPDX-License-Identifier: Apache-2.0
"""Test package.

Snapshot tests compare the checkout's ``plans/`` directory and the generated
views committed next to it. Pop ``AGENT_PLANS_DIR`` at import so ``pytest``
stays on that checkout when a shell has pointed the variable somewhere else.
``python -m planner check`` also clears the variable for the unit-test half,
after the validate half has scanned the override.
"""

import os

os.environ.pop("AGENT_PLANS_DIR", None)
