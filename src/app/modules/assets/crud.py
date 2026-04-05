from fastcrud import FastCRUD

from .model import Asset
from .schema import AssetCreateInternal, AssetDelete, AssetRead, AssetUpdateInternal

CRUDAsset = FastCRUD[
    Asset,
    AssetCreateInternal,
    AssetUpdateInternal,
    AssetUpdateInternal,
    AssetDelete,
    AssetRead,
]
crud_assets = CRUDAsset(Asset)
