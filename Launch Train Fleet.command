#!/bin/zsh
# Finder opens .command files in Terminal; always resolve paths beside this file.
cd -- "${0:A:h}" || exit 1
if [[ ! -x .venv/bin/python ]]; then
    print 'The Python environment is missing. Set it up once in this folder:'
    print 'python3 -m venv .venv'
    print '.venv/bin/python -m pip install -r requirements.txt'
    read '?Press Return to close…'
    exit 1
fi
.venv/bin/python fleet_launcher.py
result=$?
if (( result != 0 )); then
    print '\nThe launcher could not finish. See the error above.'
    read '?Press Return to close…'
fi
exit $result
