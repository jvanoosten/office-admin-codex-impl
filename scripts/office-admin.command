#!/bin/bash

cd ~/Documents/office-admin-1.1.0

(sleep 2 && open http://127.0.0.1:8000) &

uv run python main.py
