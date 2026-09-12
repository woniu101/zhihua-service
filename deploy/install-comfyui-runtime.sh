#!/usr/bin/env bash
set -euo pipefail

COMFYUI_ROOT="${COMFYUI_ROOT:-/root/ComfyUI}"
PUBLIC_MODEL_ROOT="${PUBLIC_MODEL_ROOT:-/model/ModelScope/mirror013/SeedVR2_comfyUI}"
QWEN_IMAGE_ROOT="${QWEN_IMAGE_ROOT:-/model/ModelScope/Comfy-Org/Qwen-Image_ComfyUI/split_files}"
QWEN_EDIT_ROOT="${QWEN_EDIT_ROOT:-/model/ModelScope/Comfy-Org/Qwen-Image-Edit_ComfyUI/split_files}"
H3_TURBO_ROOT="${H3_TURBO_ROOT:-/model/ModelScope/lightx2v/Minimax-h3-Turbo}"
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

for required in \
  "$QWEN_IMAGE_ROOT/diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors" \
  "$QWEN_IMAGE_ROOT/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors" \
  "$QWEN_IMAGE_ROOT/vae/qwen_image_vae.safetensors" \
  "$QWEN_EDIT_ROOT/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors" \
  "$H3_TURBO_ROOT/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors" \
  "$H3_TURBO_ROOT/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors"; do
  if [[ ! -e "$required" ]]; then
    echo "Required public model file is missing: $required" >&2
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

# Some base images contain an xformers package whose top-level import succeeds,
# while a bundled FlashAttention extension was built for an older PyTorch ABI.
# Exercise the diffusers path used by the installed nodes before keeping it.
# ComfyUI and SeedVR2 can use their working SageAttention/SDPA paths without it.
if "$PYTHON_BIN" -m pip show xformers >/dev/null 2>&1 \
  && ! "$PYTHON_BIN" -c "from diffusers import FluxTransformer2DModel" >/dev/null 2>&1; then
  "$PYTHON_BIN" -m pip uninstall -y xformers
fi

mkdir -p "$SEEDVR2_MODEL_DIR"
ln -sfn \
  "$PUBLIC_MODEL_ROOT/seedvr2_ema_3b_fp8_e4m3fn.safetensors" \
  "$SEEDVR2_MODEL_DIR/seedvr2_ema_3b_fp8_e4m3fn.safetensors"
ln -sfn \
  "$PUBLIC_MODEL_ROOT/ema_vae_fp16.safetensors" \
  "$SEEDVR2_MODEL_DIR/ema_vae_fp16.safetensors"

mkdir -p \
  "$COMFYUI_ROOT/models/diffusion_models/Qwen-Image_ComfyUI" \
  "$COMFYUI_ROOT/models/diffusion_models/Qwen-Image-Edit_ComfyUI" \
  "$COMFYUI_ROOT/models/text_encoders" \
  "$COMFYUI_ROOT/models/vae" \
  "$COMFYUI_ROOT/models/loras"
ln -sfn \
  "$QWEN_IMAGE_ROOT/diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors" \
  "$COMFYUI_ROOT/models/diffusion_models/Qwen-Image_ComfyUI/qwen_image_2512_fp8_e4m3fn.safetensors"
ln -sfn \
  "$QWEN_EDIT_ROOT/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors" \
  "$COMFYUI_ROOT/models/diffusion_models/Qwen-Image-Edit_ComfyUI/qwen_image_edit_2511_fp8mixed.safetensors"
ln -sfn \
  "$QWEN_IMAGE_ROOT/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors" \
  "$COMFYUI_ROOT/models/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors"
ln -sfn \
  "$QWEN_IMAGE_ROOT/vae/qwen_image_vae.safetensors" \
  "$COMFYUI_ROOT/models/vae/qwen_image_vae.safetensors"
ln -sfn \
  "$H3_TURBO_ROOT/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors" \
  "$COMFYUI_ROOT/models/loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
ln -sfn \
  "$H3_TURBO_ROOT/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors" \
  "$COMFYUI_ROOT/models/loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors"

echo "SeedVR2 node commit: $(git -C "$SEEDVR2_NODE_DIR" rev-parse HEAD)"
echo "SeedVR2 models: public model library links installed"
echo "Qwen Image 2512/Edit 2511: public model library links installed"
echo "H3 Turbo FL2V 8-step and Ref2V 4-step LoRAs: public model library links installed"
echo "Restart ComfyUI to load the installed nodes."
