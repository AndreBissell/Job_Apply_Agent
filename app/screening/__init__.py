"""The Quick Apply question bank (plan §10.1, Phase 9).

identity.py  Seek id families, text normalisation, the bank identity key
sort.py      sorting layers 2-4 (library id table, `user` keywords, text templates)
bank.py      upsert a job's captured questions into the bank (layer 1 = bank hit),
             the review list and corrections
classify.py  layer 5: the small model sorts what layers 1-4 left unknown, once per row
assist.py    9b: which jobs get help, and per assisted question what the job wants
             beside what the profile has (years from dates, skill in a role,
             multi-select labels), plus the wanted-but-missing gap cards

Capture (bank.py) never calls a model. Only assist.py can, through classify.py, for a
job with a full-pipeline letter.
"""
