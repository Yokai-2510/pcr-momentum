#!/bin/sh
# PCR Momentum graceful shutdown:
#   1. raise the graceful_shutdown flag (informational)
#   2. publish XADD system:stream:control {event: graceful_shutdown}
#   3. wait 60 s for engines to self-exit per Sequential_Flow.md §14
#   4. stop the stack target (SIGTERM, then SIGKILL after TimeoutStopSec)
#   5. stop pcr-init.service so RemainAfterExit-active state clears
#      and tomorrow morning the timer actually re-runs init
set -e
SOCK=/var/run/redis/redis.sock
redis-cli -s "$SOCK" SET system:flags:graceful_shutdown_initiated true >/dev/null
redis-cli -s "$SOCK" XADD system:stream:control "*" event graceful_shutdown >/dev/null
sleep 60
systemctl stop pcr-stack.target
systemctl stop pcr-init.service
exit 0
