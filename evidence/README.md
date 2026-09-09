Instruments behind the numbers in ../README.md ("The three unknowns").
- turn_census.py      per session: completedTurns vs transcript human turns, interrupt markers, API errors, compactions, origin kinds.
- promptid_probe2.py  per session: turns keyed by promptId, with interrupted / API-error attribution (the table quoted for unknown 1).
- lastactivity_probe.txt  60 s sampling of the working session's state file vs transcript growth (lastActivityAt moves with the transcript).
Run with /usr/bin/python3; both scripts are read-only.
