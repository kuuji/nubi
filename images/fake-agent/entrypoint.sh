#!/bin/sh
# Fake agent for integration tests.
# Sleeps briefly so the Job is observable as Running, then exits.
# Configure via env vars:
#   NUBI_FAKE_SLEEP      - seconds to sleep (default: 2)
#   NUBI_FAKE_EXIT_CODE  - exit code (default: 0, set to 1 for failure scenarios)
sleep "${NUBI_FAKE_SLEEP:-2}"
exit "${NUBI_FAKE_EXIT_CODE:-0}"
