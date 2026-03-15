#!/bin/bash
cd "$(dirname "$0")"
source ../venv/bin/activate
python manage.py run_worker
