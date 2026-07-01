# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations


class WizardAborted(Exception):
    """Raised when the user declines to continue past a warn-and-confirm
    prompt (e.g. failed prerequisites in CheckPrerequisites). Caught at the
    CLI boundary to exit cleanly instead of showing a raw traceback — this is
    an expected, user-chosen outcome, not a crash."""
