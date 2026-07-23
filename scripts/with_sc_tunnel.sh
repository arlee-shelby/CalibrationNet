#!/bin/bash
# Open the SSH tunnel to the slow-controls database, wait until it's
# actually up, run the given command, then tear the tunnel down.
#
#   ./scripts/with_sc_tunnel.sh python scripts/ingest_run.py 8622
#
# Works interactively and inside an SBATCH script. Uses key-based auth
# (BatchMode) — set up ssh keys for both hops rather than sshpass. A
# ~/.ssh/config entry keeps this script free of usernames:
#
#   Host sns-analysis
#       HostName analysis.sns.gov
#       User <your-sns-username>
#   Host bl13-replay
#       HostName bl13-replay.sns.gov
#       User nabreplay
#       ProxyJump sns-analysis
#
# Overridable via environment:
SC_LOCAL_PORT=${SC_LOCAL_PORT:-15432}
SC_TUNNEL_HOST=${SC_TUNNEL_HOST:-bl13-replay}
SC_REMOTE_PORT=${SC_REMOTE_PORT:-5432}

set -euo pipefail

if [ $# -eq 0 ]; then
    echo "usage: $0 <command> [args...]" >&2
    exit 1
fi

ssh -N \
    -o BatchMode=yes \
    -o ExitOnForwardFailure=yes \
    -L "${SC_LOCAL_PORT}:localhost:${SC_REMOTE_PORT}" \
    "${SC_TUNNEL_HOST}" &
SSH_PID=$!

cleanup() {
    kill "$SSH_PID" 2>/dev/null || true
}
trap cleanup EXIT

# Wait for the forwarded port to accept connections (max ~30 s) instead
# of sleeping a fixed amount.
for _ in $(seq 1 30); do
    if ! kill -0 "$SSH_PID" 2>/dev/null; then
        echo "SSH tunnel process exited — check keys/jump host." >&2
        exit 1
    fi
    if (exec 3<>"/dev/tcp/127.0.0.1/${SC_LOCAL_PORT}") 2>/dev/null; then
        exec 3>&- 3<&-
        break
    fi
    sleep 1
done

"$@"
