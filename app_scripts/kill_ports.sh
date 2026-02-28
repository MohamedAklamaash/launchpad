#!/bin/bash

PORT_RANGES=("5001 5003" "8000 8003")

for RANGE in "${PORT_RANGES[@]}"; do
    read START END <<< "$RANGE"

    for ((PORT=$START; PORT<=$END; PORT++)); do
        PIDS=$(lsof -ti tcp:$PORT)

        if [ -n "$PIDS" ]; then
            echo "Killing processes on port $PORT: $PIDS"
            kill -9 $PIDS
        else
            echo "No process running on port $PORT"
        fi
    done
done

echo "Done."
