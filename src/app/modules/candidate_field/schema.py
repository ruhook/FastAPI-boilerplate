from pydantic import BaseModel


class CandidateFieldCatalogItemRead(BaseModel):
    key: str
    label: str
    dictionary_key: str | None = None
