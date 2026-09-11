from pathlib import Path

import pytest

from zhihua_service.config import DEFAULT_WORKFLOWS
from zhihua_service.services.workflows import WorkflowRegistry, WorkflowTemplateError

WORKFLOW_DIR = Path(__file__).parents[1] / "workflows"


def _parameters(workflow_id: str) -> dict[str, object]:
    values: dict[str, object] = {
        "prompt": "A calm educational animation",
        "negativePrompt": "blurry, distorted, watermark",
        "seed": 42,
        "width": 1344,
        "height": 768,
        "visibleWidth": 1344,
        "visibleHeight": 756,
        "cropX": 0,
        "cropY": 6,
        "length": 124,
        "outputPrefix": "video/zhihua/project/scene/job",
        "firstFrameFile": "zhihua-inputs/first.png",
        "lastFrameFile": "zhihua-inputs/last.png",
        "referenceVideoFile": "zhihua-inputs/reference.mp4",
        "referenceImageFile": "zhihua-inputs/reference.png",
        "sourceVideoFile": "zhihua-inputs/source.mp4",
        "sourceImageFile": "zhihua-inputs/source.png",
    }
    return values


def test_all_accepted_workflows_are_installed_and_buildable() -> None:
    registry = WorkflowRegistry(str(WORKFLOW_DIR))
    assert registry.available_workflows(DEFAULT_WORKFLOWS) == list(DEFAULT_WORKFLOWS)
    for workflow_id in DEFAULT_WORKFLOWS:
        prompt = registry.build_prompt(workflow_id, _parameters(workflow_id))
        assert prompt
        encoded = str(prompt)
        assert "zhihua_validation" not in encoded
        assert "t2i-1" not in encoded


def test_h3_audio_can_be_removed_from_the_candidate_container() -> None:
    registry = WorkflowRegistry(str(WORKFLOW_DIR))
    parameters = _parameters("h3-t2v-turbo-v1")
    parameters["discardH3Audio"] = True
    prompt = registry.build_prompt("h3-t2v-turbo-v1", parameters)
    create_video = next(node for node in prompt.values() if node.get("class_type") == "CreateVideo")
    assert "audio" not in create_video["inputs"]


def test_qwen_image_workflows_bind_only_reviewed_generation_fields() -> None:
    registry = WorkflowRegistry(str(WORKFLOW_DIR))
    parameters = _parameters("qwen-image-generate-v1")
    parameters.update({"prompt": "一只红色机器人", "width": 768, "height": 1024, "seed": 7})
    prompt = registry.build_prompt("qwen-image-generate-v1", parameters)
    assert prompt["5"]["inputs"]["text"] == "一只红色机器人"
    assert prompt["7"]["inputs"]["width"] == 768
    assert prompt["7"]["inputs"]["height"] == 1024
    assert prompt["8"]["inputs"]["seed"] == 7
    assert prompt["1"]["inputs"]["unet_name"].endswith("qwen_image_2512_fp8_e4m3fn.safetensors")

    parameters.update({"sourceImageFile": "zhihua-inputs/frame.png", "prompt": "将天空改成晴天"})
    edited = registry.build_prompt("qwen-image-edit-v1", parameters)
    assert edited["1"]["inputs"]["image"] == "zhihua-inputs/frame.png"
    assert edited["8"]["inputs"]["prompt"] == "将天空改成晴天"
    assert edited["3"]["inputs"]["unet_name"].endswith("qwen_image_edit_2511_fp8mixed.safetensors")


@pytest.mark.parametrize(
    "workflow_id", [item for item in DEFAULT_WORKFLOWS if item.startswith("h3-")]
)
def test_h3_candidates_are_cropped_to_the_visible_project_frame(workflow_id: str) -> None:
    registry = WorkflowRegistry(str(WORKFLOW_DIR))
    prompt = registry.build_prompt(workflow_id, _parameters(workflow_id))
    crop = next(node for node in prompt.values() if node.get("class_type") == "ImageCrop")
    create_video = next(node for node in prompt.values() if node.get("class_type") == "CreateVideo")
    assert crop["inputs"] == {
        "image": ["10", 0],
        "width": 1344,
        "height": 756,
        "x": 0,
        "y": 6,
    }
    assert create_video["inputs"]["images"] == ["30", 0]


def test_seedvr2_preserves_the_candidate_frame_without_a_second_crop() -> None:
    registry = WorkflowRegistry(str(WORKFLOW_DIR))
    prompt = registry.build_prompt("seedvr2-1080p-v1", _parameters("seedvr2-1080p-v1"))
    assert all(node.get("class_type") != "ImageCrop" for node in prompt.values())
    upscaler = next(
        node for node in prompt.values() if node.get("class_type") == "SeedVR2VideoUpscaler"
    )
    assert upscaler["inputs"]["image"] == ["2", 0]


@pytest.mark.parametrize(
    ("workflow_id", "missing"),
    [
        ("h3-i2v-turbo-v1", "firstFrameFile"),
        ("h3-flf2v-high-v1", "lastFrameFile"),
        ("h3-ref2va-high-v1", "referenceVideoFile"),
        ("seedvr2-1080p-v1", "sourceVideoFile"),
        ("qwen-image-edit-v1", "sourceImageFile"),
    ],
)
def test_media_workflows_reject_missing_required_inputs(
    workflow_id: str,
    missing: str,
) -> None:
    registry = WorkflowRegistry(str(WORKFLOW_DIR))
    parameters = _parameters(workflow_id)
    parameters.pop(missing)
    with pytest.raises(WorkflowTemplateError, match=missing):
        registry.build_prompt(workflow_id, parameters)
