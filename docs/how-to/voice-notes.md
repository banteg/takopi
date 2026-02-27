# Voice notes

Enable transcription so voice notes become normal text runs.

## Enable transcription

=== "takopi config"

    ```sh
    takopi config set transports.telegram.voice_transcription true
    takopi config set transports.telegram.voice_transcription_echo true  # optional
    takopi config set transports.telegram.voice_transcription_model "gpt-4o-mini-transcribe"

    # local OpenAI-compatible transcription server (optional)
    takopi config set transports.telegram.voice_transcription_base_url "http://localhost:8000/v1"
    takopi config set transports.telegram.voice_transcription_api_key "local"
    ```

=== "toml"

    ```toml
    [transports.telegram]
    voice_transcription = true
    voice_transcription_echo = true  # optional, defaults to true
    voice_transcription_model = "gpt-4o-mini-transcribe" # optional
    voice_transcription_base_url = "http://localhost:8000/v1" # optional
    voice_transcription_api_key = "local" # optional
    ```

Set `OPENAI_API_KEY` in your environment (or `voice_transcription_api_key` in config).

To use a local OpenAI-compatible Whisper server, set `voice_transcription_base_url`
(and `voice_transcription_api_key` if the server expects one). This keeps engine
requests on their own base URL without relying on `OPENAI_BASE_URL`. If your server
requires a specific model name, set `voice_transcription_model` (for example,
`whisper-1`).

## Behavior

When you send a voice note, Takopi transcribes it and runs the result as a normal text message.
If transcription fails, you'll get an error message and the run is skipped.

With `voice_transcription_echo` enabled (the default), Takopi also sends a visual echo of the transcript with a 🎤 microphone emoji header, so you can verify what was heard.

## Related

- [Config reference](../reference/config.md)
