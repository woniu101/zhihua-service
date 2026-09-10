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
        prompt, bindings, required_parameters = self._load_template(workflow_id)
        missing = [name for name in required_parameters if name not in parameters]
        if missing:
            raise WorkflowTemplateError(f"workflow parameters are missing: {', '.join(missing)}")

        rendered = copy.deepcopy(prompt)
        for parameter_name, paths in bindings.items():
            if parameter_name not in parameters:
                continue
            for binding_path in paths:
                self._set_path(rendered, binding_path, parameters[parameter_name])
        return rendered

    def _load_template(
        self,
        workflow_id: str,
    ) -> tuple[dict[str, Any], dict[str, list[list[Any]]], list[str]]:
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
            required_parameters = raw.get("requiredParameters", [])
            if set(raw) - {"prompt", "bindings", "description", "requiredParameters"}:
                raise WorkflowTemplateError("workflow template has unsupported fields")
        else:
            prompt = raw
            bindings = {}
            required_parameters = []

        if not isinstance(prompt, dict) or not prompt:
            raise WorkflowTemplateError("workflow prompt must be a non-empty object")
        if not isinstance(bindings, dict):
            raise WorkflowTemplateError("workflow bindings must be an object")
        if not isinstance(required_parameters, list) or not all(
            isinstance(item, str) and item for item in required_parameters
        ):
            raise WorkflowTemplateError("requiredParameters must be a list of names")
        for parameter_name, paths in bindings.items():
            if (
                not isinstance(paths, list)
                or not paths
                or not all(isinstance(path, list) and path for path in paths)
            ):
                raise WorkflowTemplateError(
                    f"binding {parameter_name!r} must contain one or more paths"
                )
            for binding_path in paths:
                self._set_path(copy.deepcopy(prompt), binding_path, None)
        return prompt, bindings, required_parameters

    def available_workflows(self, accepted_workflows: tuple[str, ...]) -> list[str]:
        available: list[str] = []
        for workflow_id in accepted_workflows:
            try:
                self._load_template(workflow_id)
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
                raise WorkflowTemplateError(f"binding path does not exist: {path!r}") from exc
        final = path[-1]
        if not isinstance(final, (str, int)):
            raise WorkflowTemplateError("binding path segments must be strings or integers")
        try:
            current[final] = value
        except (KeyError, IndexError, TypeError) as exc:
            raise WorkflowTemplateError(f"binding path does not exist: {path!r}") from exc
