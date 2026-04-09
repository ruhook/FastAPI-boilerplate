from enum import StrEnum
from typing import TypedDict


class CandidateApplicationStatus(StrEnum):
    SUBMITTED = "submitted"


class CandidateApplicationStatusMeta(TypedDict):
    value: str
    cn_name: str


CANDIDATE_APPLICATION_STATUS_META: dict[str, CandidateApplicationStatusMeta] = {
    CandidateApplicationStatus.SUBMITTED.value: {
        "value": CandidateApplicationStatus.SUBMITTED.value,
        "cn_name": "已报名",
    },
}


def get_candidate_application_status_cn_name(status: str) -> str:
    return CANDIDATE_APPLICATION_STATUS_META.get(status, {}).get("cn_name", status)
