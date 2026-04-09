from pydantic import BaseModel


class CandidateFieldCatalogItemRead(BaseModel):
    key: str
    label: str
