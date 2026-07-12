# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
"""The tutor's persona_state vocabulary (docs/BRIEF_phase12_tutor.md, SRS section).

Single-writer contract: these fields are written ONLY by the tutor's assessment
strategy and read ONLY by the tutor's selection strategy. Mastery and next-due are
always DERIVED at selection time (exponential decay), never stored.
"""

STATE_LAST_PRACTICED_AT = "last_practiced_at"  # ISO date — DAY granularity by design
STATE_HALF_LIFE_DAYS = "half_life_days"        # grows on retrieval, shrinks on error
STATE_RETRIEVALS = "retrievals"                # SUCCESSFUL retrievals only, never exposures
STATE_ERRORS = "errors"
STATE_AVG_RESPONSE_LATENCY_S = "avg_response_latency_s"  # noisy proxy — weighted low
STATE_USER_INITIATED = "user_initiated"        # sticky once true
STATE_SESSIONS_PRACTICED = "sessions_practiced"
