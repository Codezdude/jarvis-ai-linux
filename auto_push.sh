#!/bin/bash
cd /home/millennium/jarvis_project/

if [[ -n $(git status -s) ]]; then
    git add .
    git commit -m "Auto-update: $(date '+%Y-%m-%d %H:%M:%S')"
    git push origin main
    echo "Changes pushed to GitHub!"
else
    echo "No changes to push."
fi
