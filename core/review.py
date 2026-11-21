"""Human dispositions annotate findings; they never change deterministic results."""
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from typing import Literal

STATUSES = {"pending": ("待复核", "Pending"), "explained": ("已说明业务原因", "Business reason recorded"),
            "correction_required": ("需核对并修正", "Correction required")}
REASONS = {"partial_shipment": ("分批交付或开票", "Partial delivery or invoicing"),
           "agreement": ("约定调整，待对照协议", "Agreed adjustment; verify agreement"),
           "entry_error": ("疑似录入错误", "Possible entry error"),
           "other": ("其他，见说明", "Other; see note")}


def file_digests(files):
    return {f.kind: hashlib.sha256(f.data).hexdigest() for f in files}


def finding_key(finding, files):
    payload = {"finding": finding.model_dump(mode="json", exclude={"id"}),
               "documents": file_digests(files)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Decision(BaseModel):
    fingerprint: str
    finding_id: str
    status: Literal["pending", "explained", "correction_required"]
    reason: Literal["partial_shipment", "agreement", "entry_error", "other"]
    note: str = Field(max_length=1000)
    reviewer: str
    time: str
    source_hashes: dict[str, str]


@dataclass
class ReviewBook:
    shipment: str = "unknown"
    decisions: dict[str, Decision] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)

    def record(self, finding, files, status, reason, note, reviewer):
        if status == "explained" and finding.category != "difference":
            raise ValueError("逻辑矛盾和无法核验项不能直接标为已说明，请补证或修改预览后复审。")
        note = note.strip()
        if status != "pending" and len(note) < 5:
            raise ValueError("请填写至少 5 个字符的复核说明，并注明需要对照的业务记录。")
        key = finding_key(finding, files)
        decision = Decision(fingerprint=key, finding_id=finding.id, status=status, reason=reason,
                            note=note, reviewer=reviewer, time=datetime.now(timezone.utc).isoformat(),
                            source_hashes=file_digests(files))
        previous = self.decisions.get(key)
        self.decisions[key] = decision
        self.events.append({"action": "review", **decision.model_dump(),
                            "previous_status": previous.status if previous else "pending"})
        return decision

    def current(self, finding, files):
        return self.decisions.get(finding_key(finding, files))

    def set_shipment(self, value, reviewer):
        if value not in {"unknown", "whole", "partial"}:
            raise ValueError("交付范围无效")
        if value != self.shipment:
            self.events.append({"action": "shipment", "before": self.shipment, "after": value,
                                "reviewer": reviewer, "time": datetime.now(timezone.utc).isoformat()})
            self.shipment = value
