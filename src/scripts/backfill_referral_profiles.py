import argparse
import asyncio
from typing import Any

from sqlalchemy import func, select

from ..app.core.db.database import async_engine, local_session
from ..app.modules.admin.admin_user.model import AdminUser  # noqa: F401
from ..app.modules.contract_record.const import CONTRACT_STATUS_ACTIVE
from ..app.modules.contract_record.model import ContractRecord
from ..app.modules.job.model import Job
from ..app.modules.referral_bonus_model.model import UserReferralProfile
from ..app.modules.referral_bonus_model.service import ensure_user_referral_profile_from_job
from ..app.modules.user.model import User


async def backfill_referral_profiles(args: argparse.Namespace) -> dict[str, Any]:
    async with local_session() as session:
        existing_result = await session.execute(
            select(UserReferralProfile.user_id).where(UserReferralProfile.is_deleted.is_(False))
        )
        existing_user_ids = {int(user_id) for user_id in existing_result.scalars().all()}

        conditions = [
            ContractRecord.contract_status == CONTRACT_STATUS_ACTIVE,
            ContractRecord.is_deleted.is_(False),
            ContractRecord.is_current.is_(True),
            Job.is_deleted.is_(False),
        ]
        if args.email:
            conditions.append(User.email == args.email.strip().lower())

        rows_result = await session.execute(
            select(ContractRecord, Job, User)
            .join(Job, Job.id == ContractRecord.job_id)
            .join(User, User.id == ContractRecord.user_id)
            .where(*conditions)
            .order_by(ContractRecord.effective_date.asc(), ContractRecord.id.asc())
        )
        rows = rows_result.all()

        targets: list[tuple[ContractRecord, Job, User]] = []
        seen_user_ids: set[int] = set()
        for contract, job, user in rows:
            user_id = int(user.id)
            if user_id in seen_user_ids or user_id in existing_user_ids:
                continue
            seen_user_ids.add(user_id)
            targets.append((contract, job, user))
            if args.limit and len(targets) >= args.limit:
                break

        summary: dict[str, Any] = {
            "apply": bool(args.apply),
            "email": args.email,
            "active_contract_rows": len(rows),
            "existing_referral_profiles": len(existing_user_ids),
            "missing_profile_targets": len(targets),
            "examples": [
                {
                    "user_id": int(user.id),
                    "email": user.email,
                    "contract_record_id": int(contract.id),
                    "agreement_ref_no": contract.agreement_ref_no,
                    "job_id": int(job.id),
                    "job_title": job.title,
                }
                for contract, job, user in targets[:10]
            ],
        }

        if not args.apply:
            return summary

        created = 0
        for contract, job, user in targets:
            await ensure_user_referral_profile_from_job(
                user_id=int(user.id),
                job=job,
                db=session,
                admin_user_id=None,
                contract_record=contract,
            )
            created += 1
        await session.commit()

        total_profiles = await session.execute(
            select(func.count()).select_from(UserReferralProfile).where(UserReferralProfile.is_deleted.is_(False))
        )
        summary["created_referral_profiles"] = created
        summary["total_referral_profiles_after"] = int(total_profiles.scalar_one())
        return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill referral eligibility profiles from active contracts.")
    parser.add_argument("--apply", action="store_true", help="Write missing referral profiles. Omit for dry-run.")
    parser.add_argument("--email", default=None, help="Backfill one candidate email only.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum profiles to create.")
    return parser.parse_args()


async def main() -> None:
    try:
        summary = await backfill_referral_profiles(parse_args())
        for key, value in summary.items():
            print(f"{key}: {value}")
    finally:
        await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
