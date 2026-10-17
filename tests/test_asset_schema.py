"""
test_asset_schema.py

Pure unit tests for the AssetCreate schema's GPS/edge_id validation —
no database, no HTTP, just Pydantic. See main.py:AssetCreate.
"""

import pytest
from pydantic import ValidationError

from main import AssetCreate
from models import AssetType

BASE_FIELDS = dict(
    eic_code="17W-UNITTEST-01-X",
    name="Unit Test Asset",
    asset_type=AssetType.BATTERY,
    max_capacity_mwh=1.0,
    max_charge_rate_mw=0.5,
    max_discharge_rate_mw=0.5,
)


def test_gps_and_edge_id_are_optional():
    asset = AssetCreate(**BASE_FIELDS)
    assert asset.latitude is None
    assert asset.longitude is None
    assert asset.edge_id is None


def test_accepts_valid_gps_and_edge_id():
    asset = AssetCreate(**BASE_FIELDS, latitude=48.8566, longitude=2.3522, edge_id="edge0")
    assert asset.latitude == 48.8566
    assert asset.longitude == 2.3522
    assert asset.edge_id == "edge0"


@pytest.mark.parametrize("latitude", [90.0, -90.0, 0.0])
def test_latitude_boundary_values_are_valid(latitude):
    asset = AssetCreate(**BASE_FIELDS, latitude=latitude)
    assert asset.latitude == latitude


@pytest.mark.parametrize("longitude", [180.0, -180.0, 0.0])
def test_longitude_boundary_values_are_valid(longitude):
    asset = AssetCreate(**BASE_FIELDS, longitude=longitude)
    assert asset.longitude == longitude


@pytest.mark.parametrize("latitude", [90.0001, -90.0001, 200, -1000])
def test_latitude_out_of_bounds_is_rejected(latitude):
    with pytest.raises(ValidationError):
        AssetCreate(**BASE_FIELDS, latitude=latitude)


@pytest.mark.parametrize("longitude", [180.0001, -180.0001, 500, -1000])
def test_longitude_out_of_bounds_is_rejected(longitude):
    with pytest.raises(ValidationError):
        AssetCreate(**BASE_FIELDS, longitude=longitude)


def test_edge_id_accepts_any_string():
    asset = AssetCreate(**BASE_FIELDS, edge_id="edge0")
    assert asset.edge_id == "edge0"
