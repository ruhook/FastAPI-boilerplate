from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.app.modules.contract_record.schema import ContractRecordListItemRead
from src.app.modules.job_progress import service as job_progress_service

pytestmark = pytest.mark.no_database_cleanup


def _contract_record(**overrides: object) -> SimpleNamespace:
    values = {
        "id": 101,
        "user_id": 202,
        "talent_profile_id": None,
        "application_id": 303,
        "job_id": 404,
        "job_progress_id": 505,
        "service_customer_company_id": 606,
        "service_customer_project_id": 707,
        "agreement_ref_no": "TL-001",
        "contract_status": "Active",
        "contract_type": "team_leader",
        "contractor_name": "Team Lead Candidate",
        "rate": Decimal("5.00"),
        "base_pay": Decimal("418.88"),
        "legal_entity": "T-Maxx International",
        "worker_type": "Contractor",
        "effective_date": date(2026, 6, 12),
        "end_date": date(2026, 12, 31),
        "draft_contract_asset_id": None,
        "candidate_signed_contract_asset_id": None,
        "company_sealed_contract_asset_id": None,
        "contract_attachment_asset_id": None,
        "parse_status": "pending",
        "parse_error": None,
        "data": {},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _progress() -> SimpleNamespace:
    return SimpleNamespace(id=505)


def test_candidate_contract_record_data_includes_team_leader_base_pay() -> None:
    payload = job_progress_service._serialize_contract_record_data(
        progress=_progress(),
        contract_record=_contract_record(),
        asset_map={},
        current_company_name="ByteDance",
        current_project_name="Any Project",
    ).model_dump()

    assert payload["contract_type"] == "team_leader"
    assert payload["base_pay"] == "418.88"


def test_admin_contract_item_exposes_base_pay() -> None:
    payload = ContractRecordListItemRead(
        id=101,
        version=1,
        is_current=True,
        user_id=202,
        job_id=404,
        job_progress_id=505,
        contract_status="Active",
        contract_type="team_leader",
        contractor_name="Team Lead Candidate",
        contractor_email="lead@example.com",
        rate=Decimal("5.00"),
        base_pay=Decimal("418.88"),
        legal_entity="T-Maxx International",
        worker_type="Contractor",
        created_at=datetime(2026, 6, 12, 10, 0, 0),
    ).model_dump()

    assert payload["base_pay"] == Decimal("418.88")
