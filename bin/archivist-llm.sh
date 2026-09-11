#!/bin/bash
# The Archivist's own model server — its own process, its own port, CPU only (the GPU is the beings').
# Never the beings' server (8080): it is sized for six slots, three breaths and three dialogues.
BIN=/home/erikxanderharvard/LLM/finetune/llama.cpp/build-cuda/bin/llama-server
GGUF=/usr/share/ollama/.ollama/models/blobs/sha256-2049f5674b1e92b4464e5729975c9689fcfbf0b0e4443ccf10b5339f370f9a54   # Qwen2.5-14B instruct, Q4_K_M
export LD_LIBRARY_PATH=/home/erikxanderharvard/cuda-12.8/lib64
exec "$BIN" -m "$GGUF" --host 127.0.0.1 --port 8090 -c 16384 -np 1 -ngl 0 -t 24 --jinja --alias archivist-14b
