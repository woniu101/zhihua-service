#!/usr/bin/env bash
set -euo pipefail

COMFYUI_ROOT="${COMFYUI_ROOT:-/root/ComfyUI}"
PUBLIC_MODEL_ROOT="${PUBLIC_MODEL_ROOT:-/model/ModelScope/mirror013/SeedVR2_comfyUI}"
PYTHON_BIN="${PYTHON_BIN:-/root/miniconda3/bin/python}"
SEEDVR2_REPOSITORY="https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler.git"
SEEDVR2_COMMIT="4490bd1f482e026674543386bb2a4d176da245b9"
SEEDVR2_NODE_DIR="$COMFYUI_ROOT/custom_nodes/ComfyUI-SeedVR2_VideoUpscaler"
SEEDVR2_MODEL_DIR="$COMFYUI_ROOT/models/SEEDVR2"

for required in \
  "$COMFYUI_ROOT/main.py" \
  "$PUBLIC_MODEL_ROOT/seedvr2_ema_3b_fp8_e4m3fn.safetensors" \
  "$PUBLIC_MODEL_ROOT/ema_vae_fp16.safetensors"; do
  if [[ ! -e "$required" ]]; then
    echo "Required runtime file is missing: $required" >&2
    exit 1
  fi
done

if [[ ! -d "$SEEDVR2_NODE_DIR/.git" ]]; then
  rm -rf "$SEEDVR2_NODE_DIR"
  git clone "$SEEDVR2_REPOSITORY" "$SEEDVR2_NODE_DIR"
fi
git -C "$SEEDVR2_NODE_DIR" fetch --depth 1 origin "$SEEDVR2_COMMIT"
git -C "$SEEDVR2_NODE_DIR" checkout --detach "$SEEDVR2_COMMIT"
"$PYTHON_BIN" -m pip install -r "$SEEDVR2_NODE_DIR/requirements.txt"

# Some base images contain an xformers extension built for an older PyTorch.
# Remove it only when the installed extension cannot be imported. ComfyUI and
# this SeedVR2 workflow then use their working SageAttention/SDPA paths.
if "$PYTHON_BIN" -m pip show xformers >/dev/null 2>&1 \
  && ! "$PYTHON_BIN" -c "import xformers" >/dev/null 2>&1; then
  "$PYTHON_BIN" -m pip uninstall -y xformers
fi

mkdir -p "$SEEDVR2_MODEL_DIR"
ln -sfn \
  "$PUBLIC_MODEL_ROOT/seedvr2_ema_3b_fp8_e4m3fn.safetensors" \
  "$SEEDVR2_MODEL_DIR/seedvr2_ema_3b_fp8_e4m3fn.safetensors"
ln -sfn \
  "$PUBLIC_MODEL_ROOT/ema_vae_fp16.safetensors" \
  "$SEEDVR2_MODEL_DIR/ema_vae_fp16.safetensors"

echo "SeedVR2 node commit: $(git -C "$SEEDVR2_NODE_DIR" rev-parse HEAD)"
echo "SeedVR2 models: public model library links installed"
echo "Restart ComfyUI to load the installed nodes."
