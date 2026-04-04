from fastcrud import FastCRUD

from .model import AdminDictionary
from .schema import (
    DictionaryCreateInternal,
    DictionaryDelete,
    DictionaryRead,
    DictionaryUpdate,
    DictionaryUpdateInternal,
)

CRUDAdminDictionary = FastCRUD[
    AdminDictionary,
    DictionaryCreateInternal,
    DictionaryUpdate,
    DictionaryUpdateInternal,
    DictionaryDelete,
    DictionaryRead,
]
crud_dictionaries = CRUDAdminDictionary(AdminDictionary)

