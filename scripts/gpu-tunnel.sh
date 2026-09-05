#!/usr/bin/env bash
# Publishes the GPU box's local Ollama to the VPS, so Allys can borrow it.
#
# It binds on the VPS side to the docker0 gateway (172.17.0.1), which is
# reachable from every container but not from the internet. Requires
# "GatewayPorts clientspecified" in the VPS sshd_config.
#
# Stop the script (or shut the machine down) and the port disappears; Allys
# notices within OLLAMA_GPU_PROBE_SECONDS and falls back to the VPS brain.
set -euo pipefail

SSH_HOST="${ALLYS_SSH_HOST:-vps}"
BIND_ADDR="${ALLYS_TUNNEL_BIND:-172.17.0.1}"
REMOTE_PORT="${ALLYS_TUNNEL_PORT:-11435}"
LOCAL_OLLAMA="${ALLYS_LOCAL_OLLAMA:-127.0.0.1:11434}"

if ! curl -fsS --max-time 3 "http://${LOCAL_OLLAMA}/api/tags" >/dev/null; then
  echo "Ollama non risponde su ${LOCAL_OLLAMA}. Avvialo con: sudo systemctl start ollama" >&2
  exit 1
fi

echo "Allys puo' usare questa GPU finche' questo processo resta aperto."
exec ssh -N \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=20 \
  -o ServerAliveCountMax=3 \
  -R "${BIND_ADDR}:${REMOTE_PORT}:${LOCAL_OLLAMA}" \
  "${SSH_HOST}"
