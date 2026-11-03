# Fine-tuning

AI Workspace turns your own chats into a training set and runs the training at a provider, because the Workbench
container is deliberately small and CPU-only. The page is **Advanced > Fine-tune**; the API is `/api/finetune/*`.

## What goes into a dataset

One conversation is one example: your messages and the assistant's answers, in order. Left out on purpose: tool calls
and tool output (web pages, email and files are third-party content), system prompts, memories, and anything before
your first message or after the last answer. Every message passes through the same secret redaction as the logs.
The file is chat JSONL, one `{"messages": [...]}` per line.

## Hosted training

Pick a dataset (10 examples or more), a connection whose provider offers OpenAI-style fine-tuning (`/files` and
`/fine_tuning/jobs`: OpenAI, Mistral, or a compatible server) and a base model the provider can fine-tune. The job
costs money at the provider. Use **Refresh** to check on it; when it succeeds, the new model is added to that
connection's models and shows in every model picker as `<connection>/<model>`.

## Training on your own GPU

Download the dataset (**JSONL** button). The format is read directly by:

- **MLX-LM** (Apple silicon): put it at `data/train.jsonl` (and a few lines in `data/valid.jsonl`), then
  `mlx_lm.lora --model mlx-community/Qwen2.5-7B-Instruct-4bit --train --data data --iters 600`, and
  `mlx_lm.fuse` to merge the adapter.
- **Unsloth / TRL**: `load_dataset("json", data_files="dataset.jsonl")`, then `SFTTrainer` with the model's chat
  template (the `messages` column is the conversational format both expect).
- **Axolotl**: `datasets: [{path: dataset.jsonl, type: chat_template}]`.

To use the result here, convert it to GGUF and load it in Ollama (`ollama create my-model -f Modelfile`), or serve it
with any OpenAI compatible server; it then appears under that connection in **Models**.
