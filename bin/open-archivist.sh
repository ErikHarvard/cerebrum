#!/bin/bash
# At login: wait for the Archivist's page (its user service starts with the session), then open it in Brave.
URL="http://127.0.0.1:8765/"
for i in $(seq 1 120); do curl -s -m 2 -o /dev/null "$URL" && break; sleep 2; done
exec brave-browser --new-window "$URL"
