import asyncio
import logging
from typing import Any

from sqlalchemy import select

from ..app.core.db.database import async_engine, local_session
from ..app.modules.admin.dictionary.model import AdminDictionary
from ..app.modules.admin.form_template.model import AdminFormTemplate

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FIELD_CATALOG_KEY = "candidate_field_catalog"
TEMPLATE_NAME = "基础候选人报名模板"


FIELD_CATALOG_OPTIONS = [
    {"label": "姓名", "value": "full_name"},
    {"label": "邮箱", "value": "email"},
    {"label": "WhatsApp", "value": "whatsapp"},
    {"label": "长期居住国家", "value": "country_of_residence"},
    {"label": "国籍/公民身份", "value": "nationality"},
    {"label": "母语级语言", "value": "native_languages"},
    {"label": "其他熟练语言", "value": "additional_languages"},
    {"label": "年龄区间", "value": "age_range"},
    {"label": "每日最大工作时长", "value": "max_working_hours_per_day"},
    {"label": "是否接受时薪结算", "value": "accepts_hourly_payment"},
    {"label": "期望时薪（USD/小时）", "value": "expected_salary_usd_per_hour"},
    {"label": "当前学历状态", "value": "education_status"},
    {"label": "AI数据标注经验", "value": "ai_data_annotation_experience"},
    {"label": "是否需要签证支持", "value": "requires_visa_sponsorship"},
    {"label": "英文简历附件", "value": "resume_attachment"},
    {"label": "岗位来源渠道", "value": "job_source"},
    {"label": "其他补充信息", "value": "additional_information"},
]


DICTIONARY_DEFINITIONS: list[dict[str, Any]] = [
    {
        "key": FIELD_CATALOG_KEY,
        "label": "候选人字段标识",
        "options": FIELD_CATALOG_OPTIONS,
    },
    {
        "key": "candidate_age_range",
        "label": "候选人年龄区间",
        "options": [
            {"label": "18岁以下", "value": "under_18"},
            {"label": "18-25岁", "value": "18_25"},
            {"label": "26-30岁", "value": "26_30"},
            {"label": "31-35岁", "value": "31_35"},
            {"label": "36-40岁", "value": "36_40"},
            {"label": "41-45岁", "value": "41_45"},
            {"label": "46-50岁", "value": "46_50"},
            {"label": "51-55岁", "value": "51_55"},
            {"label": "56-60岁", "value": "56_60"},
            {"label": "61-65岁", "value": "61_65"},
            {"label": "66-75岁", "value": "66_75"},
            {"label": "75岁以上", "value": "over_75"},
        ],
    },
    {
        "key": "candidate_max_working_hours_per_day",
        "label": "候选人每日最大工作时长",
        "options": [
            {"label": "8小时以上", "value": "over_8_hours"},
            {"label": "4-8小时", "value": "4_8_hours"},
            {"label": "少于4小时", "value": "under_4_hours"},
        ],
    },
    {
        "key": "candidate_accepts_hourly_payment",
        "label": "候选人是否接受时薪结算",
        "options": [
            {"label": "接受", "value": "yes"},
            {"label": "不接受，请说明原因", "value": "no_state_reason"},
        ],
    },
    {
        "key": "candidate_expected_salary_usd_per_hour",
        "label": "候选人期望时薪（USD/小时）",
        "options": [
            {"label": "2-5美元/小时", "value": "2_5"},
            {"label": "6-10美元/小时", "value": "6_10"},
            {"label": "11-15美元/小时", "value": "11_15"},
            {"label": "16-20美元/小时", "value": "16_20"},
            {"label": "20美元/小时以上", "value": "over_20"},
        ],
    },
    {
        "key": "candidate_education_status",
        "label": "候选人学历状态",
        "options": [
            {"label": "高中在读", "value": "high_school_in_progress"},
            {"label": "高中毕业", "value": "high_school_completed"},
            {"label": "本科在读", "value": "bachelor_in_progress"},
            {"label": "本科毕业", "value": "bachelor_completed"},
            {"label": "硕士在读", "value": "master_in_progress"},
            {"label": "硕士毕业", "value": "master_completed"},
            {"label": "博士", "value": "phd"},
        ],
    },
    {
        "key": "candidate_ai_data_annotation_experience",
        "label": "候选人AI数据标注经验",
        "options": [
            {"label": "0-3个月", "value": "0_3_months"},
            {"label": "3-6个月", "value": "3_6_months"},
            {"label": "6-12个月", "value": "6_12_months"},
            {"label": "1-2年", "value": "1_2_years"},
            {"label": "2-3年", "value": "2_3_years"},
            {"label": "3年以上", "value": "over_3_years"},
        ],
    },
    {
        "key": "candidate_visa_sponsorship_requirement",
        "label": "候选人签证支持需求",
        "options": [
            {
                "label": "不需要，我现在和未来都不需要签证支持",
                "value": "no_sponsorship_required",
            },
            {"label": "需要，我需要签证支持", "value": "sponsorship_required"},
            {"label": "其他", "value": "other"},
        ],
    },
    {
        "key": "candidate_job_source",
        "label": "候选人岗位来源渠道",
        "options": [
            {"label": "LinkedIn职位发布", "value": "linkedin_job_post"},
            {"label": "Indeed职位发布", "value": "indeed_job_post"},
            {"label": "JobThai", "value": "jobthai"},
            {"label": "在职T-Maxx数据标注员推荐", "value": "referral_from_current_annotator"},
            {"label": "其他", "value": "other"},
        ],
    },
]


FIELD_DESCRIPTIONS = {
    "whatsapp": "If we cannot connect you via email, we may try this way.",
    "country_of_residence": "Please enter the country name in English, such as United Kingdom, the Philippines, or Brazil.",
    "native_languages": "e.g. English, Malay, Korea",
    "age_range": "Required for internal analysis only; this information will not be used for selection decisions. We welcome applicants of all age range groups who pass the test~",
    "expected_salary_usd_per_hour": "Please choose your expected rate in USD.",
    "requires_visa_sponsorship": "This is an independent contractor role. Please select whether you now or in the future require visa sponsorship to work with us.",
    "resume_attachment": "Kindly ensure your resume includes a valid email address. In case the email provided in this form is incorrect, we will try to reach you via the email listed in your resume.",
}


FORM_TEMPLATE_FIELDS: list[dict[str, Any]] = [
    {
        "key": "full_name",
        "label": "Full Name",
        "type": "text",
        "required": True,
        "group": "basic",
        "canFilter": True,
        "placeholder": "Please enter your full name",
    },
    {
        "key": "email",
        "label": "Email",
        "type": "email",
        "required": True,
        "group": "basic",
        "canFilter": True,
        "placeholder": "Please enter your email",
    },
    {
        "key": "whatsapp",
        "label": "WhatsApp",
        "type": "text",
        "required": True,
        "group": "basic",
        "canFilter": False,
        "placeholder": "Please enter your WhatsApp number",
    },
    {
        "key": "country_of_residence",
        "label": "Which country do you reside in on a long-term basis?",
        "type": "text",
        "required": True,
        "group": "basic",
        "canFilter": True,
        "placeholder": "Please enter the country name in English",
    },
    {
        "key": "nationality",
        "label": "Nationality/Citizenship",
        "type": "text",
        "required": False,
        "group": "basic",
        "canFilter": True,
        "placeholder": "Please enter your nationality/citizenship",
    },
    {
        "key": "native_languages",
        "label": "Please list all your native-level languages (in English)",
        "type": "text",
        "required": True,
        "group": "basic",
        "canFilter": True,
        "placeholder": "e.g. English, Malay, Korean",
    },
    {
        "key": "additional_languages",
        "label": "Please list any additional languages you speak at a proficient level (in English).",
        "type": "text",
        "required": True,
        "group": "basic",
        "canFilter": True,
        "placeholder": "Please list additional proficient languages",
    },
    {
        "key": "age_range",
        "label": "Age Range",
        "type": "select",
        "required": True,
        "group": "basic",
        "canFilter": True,
        "dictionary_key": "candidate_age_range",
    },
    {
        "key": "max_working_hours_per_day",
        "label": "The maximum working hours per day",
        "type": "select",
        "required": True,
        "group": "work",
        "canFilter": True,
        "dictionary_key": "candidate_max_working_hours_per_day",
    },
    {
        "key": "accepts_hourly_payment",
        "label": "Do you accept to be paid by hours",
        "type": "select",
        "required": True,
        "group": "work",
        "canFilter": True,
        "dictionary_key": "candidate_accepts_hourly_payment",
    },
    {
        "key": "expected_salary_usd_per_hour",
        "label": "Expected Salary in USD (Per Hour)",
        "type": "select",
        "required": True,
        "group": "work",
        "canFilter": True,
        "dictionary_key": "candidate_expected_salary_usd_per_hour",
    },
    {
        "key": "education_status",
        "label": "What is your current education status?",
        "type": "select",
        "required": True,
        "group": "basic",
        "canFilter": True,
        "dictionary_key": "candidate_education_status",
    },
    {
        "key": "ai_data_annotation_experience",
        "label": "How many experience do you have in AI data annotation?",
        "type": "select",
        "required": True,
        "group": "work",
        "canFilter": True,
        "dictionary_key": "candidate_ai_data_annotation_experience",
    },
    {
        "key": "requires_visa_sponsorship",
        "label": "Will you now or in the future require visa sponsorship to participate in this independent contractor role?",
        "type": "select",
        "required": True,
        "group": "work",
        "canFilter": True,
        "dictionary_key": "candidate_visa_sponsorship_requirement",
    },
    {
        "key": "resume_attachment",
        "label": "Please upload your most updated comprehensive English Resume here.",
        "type": "file",
        "required": True,
        "group": "other",
        "canFilter": False,
    },
    {
        "key": "job_source",
        "label": "How did you hear about this position?",
        "type": "select",
        "required": True,
        "group": "other",
        "canFilter": True,
        "dictionary_key": "candidate_job_source",
    },
    {
        "key": "additional_information",
        "label": "Please feel free to use this space to share any additional relevant information that would support your application for this role.",
        "type": "text",
        "required": False,
        "group": "other",
        "canFilter": False,
        "placeholder": "Share any additional information",
    },
]


async def upsert_dictionary(
    *,
    key: str,
    label: str,
    options: list[dict[str, str]],
    session,
) -> AdminDictionary:
    result = await session.execute(
        select(AdminDictionary).where(
            AdminDictionary.key == key,
        )
    )
    dictionary = result.scalar_one_or_none()
    if dictionary is None:
        dictionary = AdminDictionary(
            key=key,
            label=label,
            options=options,
            data={"seed_key": key},
            is_deleted=False,
            deleted_at=None,
        )
        session.add(dictionary)
        await session.flush()
        logger.info("Created dictionary: %s", key)
        return dictionary

    dictionary.label = label
    dictionary.options = options
    dictionary.data = {**(dictionary.data or {}), "seed_key": key}
    dictionary.is_deleted = False
    dictionary.deleted_at = None
    await session.flush()
    logger.info("Updated dictionary: %s", key)
    return dictionary


def build_template_fields(dictionary_id_map: dict[str, int]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for item in FORM_TEMPLATE_FIELDS:
        field = {
            "key": item["key"],
            "label": item["label"],
            "type": item["type"],
            "required": item["required"],
            "group": item["group"],
            "canFilter": item["canFilter"],
            "placeholder": item.get("placeholder"),
        }
        dictionary_key = item.get("dictionary_key")
        if dictionary_key:
            field["dictionaryId"] = dictionary_id_map[dictionary_key]
        fields.append(field)
    return fields


async def upsert_template(*, session, dictionary_id_map: dict[str, int]) -> None:
    template_fields = build_template_fields(dictionary_id_map)
    result = await session.execute(
        select(AdminFormTemplate).where(
            AdminFormTemplate.name == TEMPLATE_NAME,
        )
    )
    template = result.scalar_one_or_none()
    template_data = {
        "seed_key": "candidate_base_application_template_v1",
        "field_catalog_dictionary_key": FIELD_CATALOG_KEY,
        "field_descriptions": FIELD_DESCRIPTIONS,
    }

    if template is None:
        template = AdminFormTemplate(
            name=TEMPLATE_NAME,
            description="基础候选人报名模板，面向 C 端英文展示。",
            fields=template_fields,
            data=template_data,
            is_deleted=False,
            deleted_at=None,
        )
        session.add(template)
        await session.flush()
        logger.info("Created form template: %s", TEMPLATE_NAME)
        return

    template.description = "基础候选人报名模板，面向 C 端英文展示。"
    template.fields = template_fields
    template.data = template_data
    template.is_deleted = False
    template.deleted_at = None
    await session.flush()
    logger.info("Updated form template: %s", TEMPLATE_NAME)


async def seed() -> None:
    async with local_session() as session:
        dictionary_id_map: dict[str, int] = {}
        for definition in DICTIONARY_DEFINITIONS:
            dictionary = await upsert_dictionary(
                key=definition["key"],
                label=definition["label"],
                options=definition["options"],
                session=session,
            )
            dictionary_id_map[definition["key"]] = dictionary.id

        await upsert_template(session=session, dictionary_id_map=dictionary_id_map)
        await session.commit()
        logger.info("Candidate base form seed completed.")


async def main() -> None:
    try:
        await seed()
    finally:
        await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
