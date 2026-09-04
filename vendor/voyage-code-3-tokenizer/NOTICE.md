# voyage-code-3 tokenizer (vendored)

## What this is

`tokenizer.json` — the tokenizer artifact (vocabulary + merge rules) for
Voyage AI's `voyage-code-3` embedding model, published by Voyage AI on
Hugging Face at https://huggingface.co/voyageai/voyage-code-3 specifically
so API callers can count tokens locally against the exact same tokenizer
Voyage's API uses server-side (see https://docs.voyageai.com/docs/tokenization:
"We open-source the tokenizers so that you can preview the tokenized
results and verify the number of tokens the API uses").

This is the tokenizer only — not the embedding model's weights, which are
never downloadable and only accessible via Voyage's paid API.

## Why vendored instead of fetched at runtime

`huggingface_hub`'s `Tokenizer.from_pretrained(...)` (used by
`sleuth/ingest/embed.py::_default_token_counter`) issues a live HTTP HEAD
request to huggingface.co on every call, even when the file is already
cached locally — confirmed live: this broke `respx`-mocked tests (which
intercept all httpx traffic, HEAD included) and would make every real
`sleuth add` ingest run silently depend on Hugging Face being reachable, a
dependency that did not exist before token-aware batching was added
(see the RPM/TPM rate-limit work in sleuth/ingest/embed.py, 2026-09-03).

Vendoring the file and loading it via `Tokenizer.from_file(path)` removes
that network dependency entirely — same reasoning already applied to
`vendor/tree-sitter-vue` in this repo (a network fetch during
`pip install`/CI is a reproducibility and reliability risk, not just a
speed concern).

## Provenance

- Source: https://huggingface.co/voyageai/voyage-code-3/blob/main/tokenizer.json
- Downloaded via `huggingface_hub`'s cache on 2026-09-03, copied verbatim
  (byte-identical to the cached blob — sha1 `443909a61d429dff23010e5bddd28ff530edda00`
  per the local HF cache's blob filename) into this directory.
- File size: 7,031,645 bytes.

## License

No separate license file is published alongside this tokenizer on its
Hugging Face model card page as of 2026-09-03. Voyage AI explicitly
publishes it for the stated purpose of letting API callers verify/count
tokens against their own account's usage — this vendoring is used
exactly for that purpose (rate-limit-aware batching in
`sleuth/ingest/embed.py`), not to redistribute or reproduce Voyage's
embedding model itself. If Voyage AI publishes explicit license terms
later, revisit this note and add them here.
