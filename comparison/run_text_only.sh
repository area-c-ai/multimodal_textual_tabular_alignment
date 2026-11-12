#!/usr/bin/env bash
# Run the TTA text-only timeline pipeline (unimodal/) on the exported shift
# summaries, talking to the owner's vLLM server through the local auth proxy
# (comparison/vllm_proxy.py). The upstream runner is NOT modified: the proxy
# injects the Authorization header, the model name and thinking-off, and the
# per-note loop works around the upstream runner reusing one log folder for
# every file in a directory run. See comparison/README.md.
set -euo pipefail
shopt -s nullglob

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SELF_DIR/.." && pwd)"
PY="$REPO_ROOT/.venv/bin/python"
RUNNER_DIR="$REPO_ROOT/unimodal"
DATA_DIR="$SELF_DIR/data"
OUT_ROOT="$SELF_DIR/outputs"
FALLBACK_ENV="$REPO_ROOT/../LLMs_for_feature_extration_test/agents/.env"

LISTEN_PORT=18001
HOST=127.0.0.1
RUN_NAME="$(date +%Y%m%d_%H%M%S)"
DRY_RUN=0
DO_CHECK=0
WANT=()

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]
  --note NAME      Process one note (note_12406 or note_12406.txt); repeatable
  --all            Process every note in comparison/data
  --run-name NAME  Run folder name (default: timestamp)
  --port N         Local proxy port (default 18001)
  --check          Offline readiness check (no network calls)
  --dry-run        Print the commands without running anything
  -h, --help       Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --note)     WANT+=("$2"); shift 2;;
    --all)      WANT+=(ALL); shift;;
    --run-name) RUN_NAME="$2"; shift 2;;
    --port)     LISTEN_PORT="$2"; shift 2;;
    --check)    DO_CHECK=1; shift;;
    --dry-run)  DRY_RUN=1; shift;;
    -h|--help)  usage; exit 0;;
    *)          echo "Unknown option: $1" >&2; usage; exit 2;;
  esac
done

# Load environment: existing shell env wins, then comparison/.env, then the
# owner's other-repo .env as a read-only fallback for VLLM_API_KEY.
load_env() {
  if [[ -z "${VLLM_API_KEY:-}" && -f "$SELF_DIR/.env" ]]; then
    set -a; . "$SELF_DIR/.env"; set +a
    echo "env: loaded $SELF_DIR/.env"
  elif [[ -z "${VLLM_API_KEY:-}" && -f "$FALLBACK_ENV" ]]; then
    set -a; . "$FALLBACK_ENV"; set +a
    echo "env: loaded $FALLBACK_ENV (fallback)"
  fi
}

run_check() {
  local failures=0 notes templates
  check() { # $1=description $2=result (0 ok)
    if [[ "$2" -eq 0 ]]; then echo "ok   : $1"; else echo "FAIL : $1"; failures=$((failures+1)); fi
  }
  local rc=0; test -x "$PY"; check "venv python at $PY" $rc
  rc=0; test -f "$RUNNER_DIR/langgraph_runner_unimodal.py"; check "unimodal runner present" $rc
  templates=("$RUNNER_DIR/templates"/*.template)
  check "5 templates in unimodal/templates (${#templates[@]} found)" $([[ ${#templates[@]} -eq 5 ]]; echo $?)
  notes=("$DATA_DIR"/*.txt)
  check "exported notes in comparison/data (${#notes[@]} found)" $([[ ${#notes[@]} -gt 0 ]]; echo $?)
  rc=0; test -f "$DATA_DIR/manifest.csv"; check "manifest.csv present" $rc
  rc=0; [[ -n "${VLLM_API_KEY:-}" ]]; check "VLLM_API_KEY set" $rc
  echo "check: ${UPSTREAM:-unset} / model ${MODEL:-unset} (offline check; no connectivity test)"
  return $failures
}

if (( DO_CHECK )); then
  load_env
  UPSTREAM="${VLLM_UPSTREAM:-http://100.73.85.24:1235}"
  MODEL="${VLLM_MODEL:-qwen3.8-27b}"
  run_check
  exit $?
fi

if (( ${#WANT[@]} == 0 )); then
  echo "error: nothing to do (use --note <id>, --all, or --help)" >&2
  usage
  exit 2
fi

# Resolve requested notes to absolute paths.
TODO=()
for want in ${WANT[@]+"${WANT[@]}"}; do
  if [[ "$want" == "ALL" ]]; then
    TODO+=("$DATA_DIR"/*.txt)
  elif [[ -f "$DATA_DIR/$want" ]]; then
    TODO+=("$DATA_DIR/$want")
  elif [[ -f "$DATA_DIR/${want}.txt" ]]; then
    TODO+=("$DATA_DIR/${want}.txt")
  else
    echo "error: no such note in $DATA_DIR: $want" >&2
    exit 2
  fi
done
if (( ${#TODO[@]} == 0 )); then
  echo "error: no note files found in $DATA_DIR" >&2
  exit 2
fi

load_env
UPSTREAM="${VLLM_UPSTREAM:-http://100.73.85.24:1235}"
MODEL="${VLLM_MODEL:-qwen3.8-27b}"
ENDPOINT="http://$HOST:$LISTEN_PORT"

# Start the auth proxy (skipped in dry-run mode).
PROXY_PID=""
if (( ! DRY_RUN )); then
  if [[ -z "${VLLM_API_KEY:-}" ]]; then
    echo "error: VLLM_API_KEY is not set (see comparison/.env.example)" >&2
    exit 3
  fi
  "$PY" "$SELF_DIR/vllm_proxy.py" --listen "$HOST:$LISTEN_PORT" \
    --upstream "$UPSTREAM" --model "$MODEL" &
  PROXY_PID=$!
  trap 'kill "$PROXY_PID" 2>/dev/null || true' EXIT
  ready=0
  for _ in $(seq 1 40); do
    if "$PY" -c "import socket; s=socket.socket(); s.settimeout(0.25); s.connect(('127.0.0.1', $LISTEN_PORT))" 2>/dev/null; then
      ready=1; break
    fi
    sleep 0.25
  done
  if (( ! ready )); then
    echo "error: proxy did not come up on $HOST:$LISTEN_PORT" >&2
    kill "$PROXY_PID" 2>/dev/null || true
    exit 4
  fi
  echo "proxy: $ENDPOINT -> $UPSTREAM (pid $PROXY_PID, model $MODEL)"
fi

FAILED=0
for path in ${TODO[@]+"${TODO[@]}"}; do
  fname="$(basename "$path")"
  stem="${fname%.txt}"
  log_dir="$OUT_ROOT/$RUN_NAME/$stem"
  cmd="$PY langgraph_runner_unimodal.py --summary_file $path \\
      --llm_endpoint $ENDPOINT --model_basename $MODEL \\
      --log_folder $log_dir --omit_tags think --n_retries 3"
  if (( DRY_RUN )); then
    echo "cd $RUNNER_DIR && $cmd"
    continue
  fi
  echo "note: $stem -> $log_dir"
  if ( cd "$RUNNER_DIR" && "$PY" langgraph_runner_unimodal.py \
        --summary_file "$path" \
        --llm_endpoint "$ENDPOINT" \
        --model_basename "$MODEL" \
        --log_folder "$log_dir" \
        --omit_tags think \
        --n_retries 3 ); then
    :
  else
    echo "FAILED: $stem" >&2
    FAILED=$((FAILED+1))
  fi
done

echo "done: ${#TODO[@]} note(s), $FAILED failure(s), outputs under $OUT_ROOT/$RUN_NAME"
if (( FAILED > 0 )); then
  exit 1
fi
