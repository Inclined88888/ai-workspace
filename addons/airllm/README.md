# AirLLM bridge (experimental, off by default)

[AirLLM](https://github.com/lyogavin/airllm) (Apache-2.0) runs a model that does not fit in your GPU's memory by loading one
layer at a time from disk. `bridge.py` serves one such model on an OpenAI-compatible address so ROOKS can connect to it.
ROOKS never installs AirLLM, downloads a model or starts the bridge. You do all three.

## What was tested, and what was not

| | Result |
|---|---|
| `pip install airllm==4.0.0` (CPU torch 2.14.1) | Installs; the virtual environment is 1.3 GB |
| Import | 28.4 s on this test machine (CPU only) |
| Load `hf-internal-testing/tiny-random-LlamaForCausalLM` | 7.6 s, including splitting the model into per-layer files |
| Generate 5 tokens | 8.3 s in a script, 5.6 s through the bridge |
| Bridge `/v1/models`, `/v1/chat/completions`, a streaming request | Answer as expected (one chunk for streaming) |
| Answer quality | **Not tested.** The test model has random weights, so its text is meaningless by design |
| A real model (7B or larger), a GPU, Apple Silicon | **Not tested.** No GPU and no time budget for a multi-gigabyte download on the test machine |

A package that installs is not a working model. Connect the bridge and run ROOKS's capability tests
(Settings, Connections) before you rely on it.

## Limits

- Slow: every token reads every layer from disk. Large models take seconds to minutes per token.
- No tool calling. The ROOKS assistant uses tools for most requests, so it can only answer from what it already knows.
  ROOKS's tool test fails for this bridge, which is correct.
- No streaming. A streaming request gets the whole answer as one chunk; ROOKS's streaming test fails, which is correct.
- One request at a time.
- No authentication. It listens on 127.0.0.1 unless you pass `--host`.

## Use

```bash
python3 -m venv ~/airllm-venv
~/airllm-venv/bin/pip install airllm==4.0.0
~/airllm-venv/bin/python bridge.py --model <hugging face id or folder> --device cuda   # or cpu, mps
```

In ROOKS, Settings, AI models: add an OpenAI-compatible server at `http://127.0.0.1:8090/v1`
(ROOKS in Docker: `http://host.docker.internal:8090/v1`), then confirm it is local and cannot bill you, and run the
capability tests.

The model's own licence applies. Check it on the model's page before you download it.
