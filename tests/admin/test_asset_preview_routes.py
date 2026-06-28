import pytest

from src.app.admin.api.v1.settings.assets import router


@pytest.mark.no_database_cleanup
def test_feishu_spreadsheet_preview_route_is_not_registered() -> None:
    assert "/assets/{asset_id}/feishu-preview" not in {route.path for route in router.routes}
