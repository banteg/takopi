# Voice notes

Enable transcription so voice notes become normal text runs.

## Enable transcription

```toml
[transports.telegram]
voice_transcription = true
voice_transcription_model = "gpt-4o-mini-transcribe" # optional
```

Set `OPENAI_API_KEY` in your environment (uses OpenAI’s transcription API).

To use a local OpenAI-compatible Whisper server, also set `OPENAI_BASE_URL`
(for example, `http://localhost:8000/v1`) and a dummy `OPENAI_API_KEY` if your server ignores it.
If your server requires a specific model name, set `voice_transcription_model` (for example, `whisper-1`).

## Behavior

When you send a voice note, Takopi transcribes it and runs the result as a normal text message.
If transcription fails, you’ll get an error message and the run is skipped.

## Related

- [Config reference](../reference/config.md)

