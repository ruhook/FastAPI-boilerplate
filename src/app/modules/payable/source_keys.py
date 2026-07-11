from uuid6 import uuid7


def salary_source_key(*, month: str, user_id: int, contract_record_id: int) -> str:
    return f"salary:{month}:{user_id}:{contract_record_id}"


def team_leader_bonus_source_key(*, month: str, user_id: int, project_id: int) -> str:
    return f"team_leader_bonus:{month}:{user_id}:{project_id}"


def referral_reward_source_key(*, referral_record_id: int, milestone_index: int) -> str:
    return f"referral_reward:{referral_record_id}:{milestone_index}"


def manual_source_key() -> str:
    return f"manual:{uuid7()}"
