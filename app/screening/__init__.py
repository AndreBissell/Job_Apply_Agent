"""The Quick Apply question bank (plan §10.1, Phase 9).

identity.py  Seek id families, text normalisation, the bank identity key
sort.py      sorting layers 2-4 (library id table, `user` keywords, text templates)
bank.py      upsert a job's captured questions into the bank (layer 1 = bank hit),
             the review list and corrections

All code, no LLM: layer 5 (the small model, once per new question) is Phase 9b.
"""
