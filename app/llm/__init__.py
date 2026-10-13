"""The LLM layer: job extraction, matching, quick screening and cover letters.

Every provider call goes through ``client.py`` (tiers, usage log, budget guard); no
other module imports a provider SDK. The main modules are ``extract``, ``match``,
``quickscreen``, ``cover_letter`` (the one-shot letter) and the ``letter/`` package
(the agent and workflow engines).
"""
