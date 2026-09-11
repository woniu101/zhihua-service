Place reviewed ComfyUI API-format workflow templates here. See the repository README.

Templates that depend on custom nodes should declare `requiredNodeTypes`. The capability endpoint
checks those names against the running ComfyUI node inventory so an incomplete image is detected
before the user submits a generation job.
