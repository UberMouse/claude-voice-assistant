# MVP smoke test

Prerequisites:
- Phase 0 spikes passed (or at least: faster-whisper runs, kokoro runs, claude --print works).
- `claude` is on `$PATH` and authenticated.
- Mic and speakers work in the current env (`parecord/paplay` round-trip succeeds).

Run:

```bash
./scripts/dev.sh
tmux attach -t voice-dev
```

Cycle through windows (Ctrl-b n) and verify each service started without error.

Manual tests (run with focus on a window that won't swallow F3):

1. **Q&A**: Press F3. Within 5s say "what's the capital of France". Wait. You should hear "Paris" (or a brief similar reply).
2. **Note**: Press F3. Say "save a note that I need to email Sam tomorrow". Confirm a file appears in `~/voice-assistant/notes/`.
3. **Recall**: Press F3. Say "what notes did I save today". Should describe the note.

Expected end-to-end latency budget:
- Mic stop -> Claude first speak: < 4s with distil-large-v3 STT on GPU
- Total Q&A turn: 3-7s on GPU; double that on CPU
