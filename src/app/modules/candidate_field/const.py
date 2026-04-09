from enum import StrEnum


class CandidateFieldKey(StrEnum):
    FULL_NAME = "full_name"
    EMAIL = "email"
    WHATSAPP = "whatsapp"
    COUNTRY_OF_RESIDENCE = "country_of_residence"
    NATIONALITY = "nationality"
    NATIVE_LANGUAGES = "native_languages"
    ADDITIONAL_LANGUAGES = "additional_languages"
    AGE_RANGE = "age_range"
    MAX_WORKING_HOURS_PER_DAY = "max_working_hours_per_day"
    ACCEPTS_HOURLY_PAYMENT = "accepts_hourly_payment"
    EXPECTED_SALARY_USD_PER_HOUR = "expected_salary_usd_per_hour"
    EDUCATION_STATUS = "education_status"
    AI_DATA_ANNOTATION_EXPERIENCE = "ai_data_annotation_experience"
    REQUIRES_VISA_SPONSORSHIP = "requires_visa_sponsorship"
    RESUME_ATTACHMENT = "resume_attachment"
    JOB_SOURCE = "job_source"
    ADDITIONAL_INFORMATION = "additional_information"


CANDIDATE_FIELD_CATALOG_DICTIONARY_KEY = "candidate_field_catalog"


CANDIDATE_FIELD_CN_NAME_MAP: dict[CandidateFieldKey, str] = {
    CandidateFieldKey.FULL_NAME: "姓名",
    CandidateFieldKey.EMAIL: "邮箱",
    CandidateFieldKey.WHATSAPP: "WhatsApp",
    CandidateFieldKey.COUNTRY_OF_RESIDENCE: "长期居住国家",
    CandidateFieldKey.NATIONALITY: "国籍/公民身份",
    CandidateFieldKey.NATIVE_LANGUAGES: "母语级语言",
    CandidateFieldKey.ADDITIONAL_LANGUAGES: "其他熟练语言",
    CandidateFieldKey.AGE_RANGE: "年龄区间",
    CandidateFieldKey.MAX_WORKING_HOURS_PER_DAY: "每日最大工作时长",
    CandidateFieldKey.ACCEPTS_HOURLY_PAYMENT: "是否接受时薪结算",
    CandidateFieldKey.EXPECTED_SALARY_USD_PER_HOUR: "期望时薪（USD/小时）",
    CandidateFieldKey.EDUCATION_STATUS: "当前学历状态",
    CandidateFieldKey.AI_DATA_ANNOTATION_EXPERIENCE: "AI数据标注经验",
    CandidateFieldKey.REQUIRES_VISA_SPONSORSHIP: "是否需要签证支持",
    CandidateFieldKey.RESUME_ATTACHMENT: "英文简历附件",
    CandidateFieldKey.JOB_SOURCE: "岗位来源渠道",
    CandidateFieldKey.ADDITIONAL_INFORMATION: "其他补充信息",
}


def build_candidate_field_catalog_options() -> list[dict[str, str]]:
    return [
        {"label": label, "value": field_key.value}
        for field_key, label in CANDIDATE_FIELD_CN_NAME_MAP.items()
    ]


CANDIDATE_FIELD_SELECT_OPTIONS_EN_MAP: dict[str, list[dict[str, str]]] = {
    CandidateFieldKey.AGE_RANGE.value: [
        {"label": "Under 18", "value": "under_18"},
        {"label": "18-25", "value": "18_25"},
        {"label": "26-30", "value": "26_30"},
        {"label": "31-35", "value": "31_35"},
        {"label": "36-40", "value": "36_40"},
        {"label": "41-45", "value": "41_45"},
        {"label": "46-50", "value": "46_50"},
        {"label": "51-55", "value": "51_55"},
        {"label": "56-60", "value": "56_60"},
        {"label": "61-65", "value": "61_65"},
        {"label": "66-75", "value": "66_75"},
        {"label": "Over 75", "value": "over_75"},
    ],
    CandidateFieldKey.MAX_WORKING_HOURS_PER_DAY.value: [
        {"label": "More than 8 hours", "value": "over_8_hours"},
        {"label": "4-8 hours", "value": "4_8_hours"},
        {"label": "Less than 4 hours", "value": "under_4_hours"},
    ],
    CandidateFieldKey.ACCEPTS_HOURLY_PAYMENT.value: [
        {"label": "Yes", "value": "yes"},
        {"label": "No, I will explain why", "value": "no_state_reason"},
    ],
    CandidateFieldKey.EXPECTED_SALARY_USD_PER_HOUR.value: [
        {"label": "USD 2-5 / hour", "value": "2_5"},
        {"label": "USD 6-10 / hour", "value": "6_10"},
        {"label": "USD 11-15 / hour", "value": "11_15"},
        {"label": "USD 16-20 / hour", "value": "16_20"},
        {"label": "Above USD 20 / hour", "value": "over_20"},
    ],
    CandidateFieldKey.EDUCATION_STATUS.value: [
        {"label": "High school in progress", "value": "high_school_in_progress"},
        {"label": "High school completed", "value": "high_school_completed"},
        {"label": "Bachelor in progress", "value": "bachelor_in_progress"},
        {"label": "Bachelor completed", "value": "bachelor_completed"},
        {"label": "Master in progress", "value": "master_in_progress"},
        {"label": "Master completed", "value": "master_completed"},
        {"label": "PhD", "value": "phd"},
    ],
    CandidateFieldKey.AI_DATA_ANNOTATION_EXPERIENCE.value: [
        {"label": "0-3 months", "value": "0_3_months"},
        {"label": "3-6 months", "value": "3_6_months"},
        {"label": "6-12 months", "value": "6_12_months"},
        {"label": "1-2 years", "value": "1_2_years"},
        {"label": "2-3 years", "value": "2_3_years"},
        {"label": "Over 3 years", "value": "over_3_years"},
    ],
    CandidateFieldKey.REQUIRES_VISA_SPONSORSHIP.value: [
        {
            "label": "No, I do not require sponsorship now or in the future",
            "value": "no_sponsorship_required",
        },
        {"label": "Yes, I require sponsorship", "value": "sponsorship_required"},
        {"label": "Other", "value": "other"},
    ],
    CandidateFieldKey.JOB_SOURCE.value: [
        {"label": "LinkedIn job post", "value": "linkedin_job_post"},
        {"label": "Indeed job post", "value": "indeed_job_post"},
        {"label": "JobThai", "value": "jobthai"},
        {
            "label": "Referral from a current T-Maxx annotator",
            "value": "referral_from_current_annotator",
        },
        {"label": "Other", "value": "other"},
    ],
}
