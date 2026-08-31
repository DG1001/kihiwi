# Fremde Bestandteile

kihiwi selbst steht unter der MIT-Lizenz (siehe [LICENSE](LICENSE)). Es lädt
zur Laufzeit Modelle und Programme, die nicht in diesem Repository liegen und
eigenen Lizenzen unterliegen:

| Bestandteil | Zweck | Lizenz |
|---|---|---|
| [whisper.cpp](https://github.com/ggml-org/whisper.cpp) | Spracherkennung | MIT |
| ggml-large-v3-turbo | Whisper-Modell | MIT |
| [Piper](https://github.com/OHF-Voice/piper1-gpl) | Sprachausgabe | GPL-3.0 |
| Piper-Stimmen (`de_DE-thorsten-*`) | Sprachausgabe | CC-BY-4.0 |
| [Silero VAD](https://github.com/snakers4/silero-vad) | Sprachaktivität | MIT |
| [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) | Sprechertrennung | Apache-2.0 |
| pyannote segmentation 3.0 (ONNX) | Sprechertrennung | MIT (CNRS) |
| 3D-Speaker CAM++ | Sprecher-Embeddings | Apache-2.0 |
| [vLLM](https://github.com/vllm-project/vllm) | Sprachmodell-Server | Apache-2.0 |
| [SearXNG](https://github.com/searxng/searxng) | Internetsuche (optional) | AGPL-3.0 |
| [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) | PDF-Text für den Index | BSD-3-Clause / Apache-2.0 |
| [pypdf](https://github.com/py-pdf/pypdf) | PDF-Text, Rückfallebene | BSD-3-Clause |
| [fastembed](https://github.com/qdrant/fastembed) | Vektorindex (optional) | Apache-2.0 |
| multilingual-e5-large | Einbettungsmodell | MIT |

**Piper steht unter GPL-3.0.** kihiwi ruft es als eigenständige Bibliothek auf
und wird nicht mit ihm ausgeliefert; wer kihiwi weitergibt, gibt Piper nicht mit
weiter. Wer ein Gesamtwerk daraus baut, prüft das für sich selbst.

Das Sprachmodell ist austauschbar: `sprachdienst/llm.py` spricht die
OpenAI-kompatible Schnittstelle (`{KIHIWI_LLM}/chat/completions`). Jeder lokale
Server, der sie anbietet — vLLM, llama.cpp, Ollama —, tut es.
