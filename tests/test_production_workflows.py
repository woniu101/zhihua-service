from pathlib import Path

import pytest

from zhihua_service.config import DEFAULT_WORKFLOWS
from zhihua_service.services.workflows import WorkflowRegistry, WorkflowTemplateError

WORKFLOW_DIR = Path(__file__).parents[1] / "workflows"


def _parameters(workflow_id: str) -> dict[str, object]:
    values: dict[str, object] = {
        "prompt": "A calm educational animation",
        "seed": 42,
        "width": 1344,
        "height": 768,
        "length": 124,
        "outputPrefix": "video/zhihua/project/scene/job",
        "firstFrameFile": "zhihua-inputs/first.png",
        "lastFrameFile": "zhihua-inputs/last.png",
        "referenceVideoFile": "zhihua-inputs/reference.mp4",
        "referenceImageFile": "zhihua-inputs/reference.png",
        "sourceVideoFile": "zhihua-inputs/source.mp4",
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
    create_video = next(
        node for node in prompt.values() if node.get("class_type") == "CreateVideo"
    )
    assert "audio" not in create_video["inputs"]


@pytest.mark.parametrize(
    ("workflow_id", "missing"),
    [
        ("h3-i2v-turbo-v1", "firstFrameFile"),
        ("h3-flf2v-high-v1", "lastFrameFile"),
        ("h3-ref2va-high-v1", "referenceVideoFile"),
        ("seedvr2-1080p-v1", "sourceVideoFile"),
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
