"""Bounded structured-output recovery. Raw model text stays in memory only."""
import json
import re
from collections import Counter

from pydantic import ValidationError


def output_schema(output_type, state):
    from agents import AgentOutputSchema
    class CapturingSchema(AgentOutputSchema):
        def validate_json(self, text):
            # Some compatible providers return valid JSON in Markdown despite
            # the schema request. Unwrap exactly ONE explicit JSON fence only;
            # never guess among multiple objects or accept a truncated object.
            blocks = re.findall(r"```json[ \t]*\r?\n(.*?)```", text, re.S | re.I)
            if len(blocks) == 1 and text.count("```") == 2:
                text = blocks[0].strip()
                state["unwrapped"] = True
            state["text"] = text[:24000]
            try:
                result = super().validate_json(text)
                state["valid"] = True
                return result
            except Exception:
                state["valid"] = False
                try:
                    output_type.model_validate_json(text, strict=True)
                except ValidationError as exc:
                    state["issues"] = [{"loc": list(e["loc"]), "type": e["type"]}
                        for e in exc.errors(include_input=False)][:12]
                raise
    return CapturingSchema(output_type)


def partial_output(text, output_type, item_types):
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(value, dict):
        return None
    sections = {}
    for key, item_type in item_types.items():
        sections[key] = []
        if isinstance(value.get(key), list):
            for item in value[key][:10]:
                # Only repair the known SDK container mismatch, preserving every
                # source reference, field value and column/field pair verbatim.
                if key == "mappings" and isinstance(item, dict) and isinstance(item.get("columns"), list):
                    item = {**item, "columns": {"proposals": item["columns"]}}
                try:
                    sections[key].append(item_type.model_validate(item, strict=True))
                except ValidationError:
                    pass
    if not any(sections.values()):
        return None
    return output_type(summary="模型完整输出未通过校验；仅保留可独立校验的候选，仍需对照原件确认。", **sections)


def constrain_repair(repaired, original):
    """A format retry cannot add/change/reassign a candidate, even if plausible."""
    if original is None:
        raise ValueError("原输出没有可独立校验的候选，请继续人工复核。")
    signature = lambda item: json.dumps(item.model_dump(), sort_keys=True, ensure_ascii=False)
    for section in ("mappings", "fields", "links"):
        allowed = Counter(signature(item) for item in getattr(original, section))
        proposed = Counter(signature(item) for item in getattr(repaired, section))
        if proposed - allowed:
            raise ValueError("格式修复改变了候选内容，已拒绝应用。")
    return repaired.model_copy(update={"summary": "模型输出格式已修复；建议内容仍需逐项对照原件确认。"})
