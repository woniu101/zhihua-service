import copy
import json
from pathlib import Path
from typing import Any


class WorkflowTemplateError(RuntimeError):
    code = "workflow_template_invalid"


class WorkflowTemplateMissingError(WorkflowTemplateError):
    code = "workflow_template_missing"


COMPATIBILITY_ALIASES = {
    "h3-fl2v-turbo-v1": "h3-flf2v-turbo-v1",
}


class WorkflowRegistry:
    """Loads ComfyUI API-format prompts selected by trusted workflow identifiers."""

    def __init__(self, workflow_directory: str) -> None:
        self._directory = Path(workflow_directory)

    def build_prompt(
        self,
        workflow_id: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        template_id = COMPATIBILITY_ALIASES.get(workflow_id, workflow_id)
        path = self._directory / f"{template_id}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise WorkflowTemplateMissingError(
                f"workflow template {workflow_id!r} is not installed"
            ) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkflowTemplateError(
                f"workflow template {workflow_id!r} cannot be read"
            ) from exc

        if not isinstance(raw, dict):
            raise WorkflowTemplateError("workflow template root must be an object")

        if "prompt" in raw:
            prompt = raw.get("prompt")
            bindings = raw.get("bindings", {})
            if set(raw) - {"prompt", "bindings", "description"}:
                raise WorkflowTemplateError("workflow template has unsupported fields")
        else:
            prompt = raw
            bindings = {}

        if not isinstance(prompt, dict) or not prompt:
            raise WorkflowTemplateError("workflow prompt must be a non-empty object")
        if not isinstance(bindings, dict):
            raise WorkflowTemplateError("workflow bindings must be an object")

        rendered = copy.deepcopy(prompt)
        for parameter_name, paths in bindings.items():
            if (
                not isinstance(paths, list)
                or not paths
                or not all(isinstance(path, list) and path for path in paths)
            ):
                raise WorkflowTemplateError(
                    f"binding {parameter_name!r} must contain one or more paths"
                )
            if parameter_name not in parameters:
                continue
            for binding_path in paths:
                self._set_path(rendered, binding_path, parameters[parameter_name])
        return rendered

    def available_workflows(self, accepted_workflows: tuple[str, ...]) -> list[str]:
        available: list[str] = []
        for workflow_id in accepted_workflows:
            try:
                self.build_prompt(workflow_id, {})
            except WorkflowTemplateError:
                continue
            available.append(workflow_id)
        return available

    @staticmethod
    def _set_path(document: dict[str, Any], path: list[Any], value: Any) -> None:
        current: Any = document
        for segment in path[:-1]:
            if not isinstance(segment, (str, int)):
                raise WorkflowTemplateError("binding path segments must be strings or integers")
            try:
                current = current[segment]
            except (KeyError, IndexError, TypeError) as exc:
                raise WorkflowTemplateError(
                    f"binding path does not exist: {path!r}"
                ) from exc
        final = path[-1]
        if not isinstance(final, (str, int)):
            raise WorkflowTemplateError("binding path segments must be strings or integers")
        try:
            current[final] = value
        except (KeyError, IndexError, TypeError) as exc:
            raise WorkflowTemplateError(
                f"binding path does not exist: {path!r}"
            ) from exc
