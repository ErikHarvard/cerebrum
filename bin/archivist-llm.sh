#!/bin/bash
# The Archivist's own model server — its own process, its own port, CPU only. Never the beings' server:
# that one is sized for their slots. This machine's paths live in state/llm/archivist-llm.env (not tracked).
HERE="$(cd "$(dirname "$0")/.." && pwd)"
ENV="${CEREBRUM_STATE:-$HERE/state}/llm/archivist-llm.env"
[ -f "$ENV" ] && . "$ENV"
: "${ARCHIVIST_LLM_BIN:?set ARCHIVIST_LLM_BIN in $ENV}" "${ARCHIVIST_LLM_MODEL:?set ARCHIVIST_LLM_MODEL in $ENV}"
[ -n "$ARCHIVIST_LLM_LIBS" ] && export LD_LIBRARY_PATH="$ARCHIVIST_LLM_LIBS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$ARCHIVIST_LLM_BIN" -m "$ARCHIVIST_LLM_MODEL" --host 127.0.0.1 --port "${ARCHIVIST_LLM_PORT:-8090}" -c "${ARCHIVIST_LLM_CTX:-16384}" -np 1 -ngl 0 -t "${ARCHIVIST_LLM_THREADS:-$(nproc)}" --jinja --alias archivist-14b
