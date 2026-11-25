"""
routers

API routes added in V1.0, one module per part of the API. The MVP routes stay
in main.py for now (whether to move them is open, SPEC.md §7).

- errors:    the 422 shape shared by the checks made against the database, and
             the app's 422 handler
- catalogue: /languages and /metrics, read and create (docs/DATA_MODEL.md §8)
- measurements: POST /sites/{site_id}/measurements, the ingestion batch (§8)
"""
