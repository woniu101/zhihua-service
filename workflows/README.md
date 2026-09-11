Place reviewed ComfyUI API-format workflow templates here. See the repository README.

Templates that depend on custom nodes should declare `requiredNodeTypes`. The capability endpoint
checks those names against the running ComfyUI node inventory so an incomplete image is detected
before the user submits a generation job.

`qwen-image-generate-v1` and `qwen-image-edit-v1` are fixed API templates reviewed from the
public-model validation run. The service only binds prompt text, seed, approved dimensions,
input filename, and output prefix; callers cannot replace nodes or model paths.
