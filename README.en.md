# kihiwi

A voice-controlled research assistant for a laboratory. Runs **entirely
locally** — no cloud service anywhere in the path, not even for testing.

A lab machine with a speakerphone and a monitor sits in the lab; the computing
happens on a separate box. The assistant records lab conversation (only when
explicitly started), answers questions from your own documents, sets timers, and
turns every recording into a protocol.

*[Deutsche Fassung](README.md) (authoritative) · [German–English glossary](GLOSSAR.md)*

> **The code is in German** — identifiers, comments, documentation. That is
> deliberate, not an oversight. This is a German-language voice assistant: the
> trigger words, the number-to-words conversion, the umlaut handling and the
> fuzzy matching against German speech-recognition output are the subject
> matter, not a layer of translation on top of it. Translating the identifiers
> would produce English names over German comments explaining German regular
> expressions — worse than either language on its own, and stale within a week.
>
> The [glossary](GLOSSAR.md) maps every term you need. If you read code, that is
> enough; the structure is conventional.

Developed on an ASUS Ascent GX10 (GB10, 121 GiB unified memory, aarch64).
Nothing is tied to it: every endpoint is an environment variable, and the
language model is reached over the OpenAI-compatible API.

## What it does

**Addressing it.** Say „Kiwi", wait for „Ja?", then speak — or say everything in
one breath. The conversation then stays open until „Danke, Kiwi" or 45 seconds
of silence.

**Trigger words** choose the path instead of letting a router guess:

| Word | Effect |
|---|---|
| **Internetsuche** | look one thing up quickly (2–3 s) |
| **Internetrecherche** | thorough research, result arrives later (30–40 s) |
| **Dokumentenrecherche** | search your own documents |
| **Wissensabgleich** | pull fresh state from repositories and cloud |
| **Hermesaufgabe** | pass the instruction verbatim to the research agent |
| **Kiwihilfe** | the list, spoken and on screen |

**Without the wake word:** „Sprachaufzeichnung starten" / „… stoppen" act
directly, so you need not call the assistant first just to record.

**Timers and reminders**, parsed in German by the service rather than the model,
and surviving a service restart.

**Protocols.** Stopping a recording produces one automatically: transcript,
absolute timestamps, summary, and — where confident — speaker labels. It goes
straight into the knowledge index, so you can ask about it immediately.

## Two principles worth stealing

**Where an action is deterministic, the service decides — not the model.**
Recording control, timers, trigger words and time expressions are resolved in
code. The reason is measured, not assumed: the same system prompt that produces
good spoken German suppressed tool calls (0 of 3 attempts with it, 3 of 3
without). A timer the model forgets to set is only discovered when it fails to
ring.

**Prefer no answer to a wrong one.** When retrieval finds nothing, the assistant
says so instead of inventing a figure. When a speaker attribution is not clear,
it is left blank. Protocols separate transcript from summary and mark the
summary as derived.

## Prerequisites

Python 3.12, whisper.cpp as a server, any OpenAI-compatible LLM server (vLLM,
llama.cpp, Ollama), Piper with a voice, Silero VAD as ONNX. Optional: SearXNG
for web search, sherpa-onnx models for speaker separation.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp vokabular.beispiel.txt vokabular.txt          # your domain vocabulary
cp wissen/quellen.beispiel.json wissen/quellen.json
./dienste.sh start                               # "services"
./dienste.sh status
```

Endpoints: `KIHIWI_LLM`, `KIHIWI_STT`, `KIHIWI_MODEL`, `KIHIWI_SEARXNG`,
`KIHIWI_BIND`, `KIHIWI_PORT`.

## A caution about recording

Continuous recording of conversation is legally restricted in many
jurisdictions; in Germany it falls under § 201 StGB and requires the consent of
everyone present. The design reflects that: a hard mute button, a large
recording indicator, raw audio kept as evidence, and recordings excluded from
the repository. This is not legal advice.

## Documentation

The three documents are German. `technisch.md` holds the architecture and every
measured number; `entwicklung.md` is a development log that records the failures
too — the synthetic test that was really testing the speech synthesis, the two
lookbehinds that check the same position, the summary that reported "everything
up to date" while two commits had just arrived. It is the most useful part.

## Licence

MIT, see [LICENSE](LICENSE). Third-party components are listed in
[DRITTANBIETER.md](DRITTANBIETER.md) — **Piper is GPL-3.0** and is not shipped
with this project.
