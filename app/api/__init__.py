"""Local FastAPI backend for the Seek Job Assistant Chrome extension.

The extension reads job data from Seek pages the user has already opened in their
real browser and POSTs it here; this app upserts listings and (later) runs LLM
extraction + matching. No automated requests are ever made to Seek from here.
"""
