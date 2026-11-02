# Frontier inference implementation plan

**Goal:** Configure Gemini inference while retaining bge-m3 embeddings and primary/secondary roles.
**Architecture:** Retain the existing Mem0/OpenAI client interface. Translate native Gemini and Anthropic requests/responses at the model HTTP boundary, sharing the translation with setup validation. Existing URL-only settings continue to mean local LM Studio.
**Scope:** Inference supports local, OpenAI-compatible, Gemini and Anthropic. Embeddings retain existing local configuration and 1024-dimensional validation. API keys remain exclusively in mode-600 mem0.json. Never change Oracle vectors or the user's installed configuration during development.

- [x] Add a local HTTP-server regression check for native request authentication, JSON schema, completion status, setup persistence, and cross-provider failover. Verify failure before implementation.
- [x] Extend model_api.py with inference connection resolution and native wire conversion, including provider-specific authentication and response normalization.
- [x] Extend configure.py with per-role local/frontier selection, provider/model/secret collection and validation before atomic save. Keep the local URL validator intact and clear obsolete credentials when returning to local.
- [x] Extend mem0_mcp.py's existing transport to route each role with its own credentials and protocol; preserve network-only failover and existing extraction validation.
- [x] Update existing interactive fixtures, installer bundle files/help and README. Run the documented local checks and inspect the final diff.

Review focus: No API keys in argv/logs; no credential forwarding between providers; incomplete/blocked outputs cannot become saved memories; failed validation cannot partially overwrite settings; bge-m3 settings and vectors remain intact.
