# Task 04 remaining

Implemented the `/model` model listing and direct name switch, `rooks models`, `/provider NAME`, the six requested built in provider entries, and fake `/models` coverage. The required gate passes.

Still missing from the spec: the model listing is not an interactive filter as you type, and selecting a numbered row does not switch the active model. The present behavior shows the numbered list and accepts a model ID as `/model NAME`. Provider source validation was only inspected for NVIDIA; the remaining five OpenClaw provider implementations still need review before treating their metadata as verified.
