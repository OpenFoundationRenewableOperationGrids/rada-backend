"""
mvp_sites.py

Transitional: gives every asset created through the MVP paths (POST /assets
and seed_batteries.py) a site, with the same rule as migration 0002
(docs/DATA_MODEL.md §6.1):

- an asset with an edge_id joins that edge's site, created on first use with
  no source connection (the link comes with BE-05);
- any other asset gets its own site, named after it, carrying its position;
- new sites go in the Demo portfolio (EUR), Europe/Paris.

Known limitation: a later PUT or PATCH of an asset's latitude, longitude or
edge_id updates the asset's MVP columns only, not its site. The MVP front end
reads the asset's columns, so nothing visible breaks.

Removed when the seed is rewritten to the demo portfolio (DATA_MODEL.md §7)
and the MVP endpoints retire.
"""

from sqlalchemy.orm import Session

from models import Portfolio, Site

DEMO_PORTFOLIO = "Demo"
DEMO_CURRENCY = "EUR"
MVP_TIMEZONE = "Europe/Paris"


def demo_portfolio(db: Session) -> Portfolio:
    """The Demo portfolio, created on first use."""
    portfolio = db.query(Portfolio).filter(Portfolio.name == DEMO_PORTFOLIO).first()
    if portfolio is None:
        portfolio = Portfolio(name=DEMO_PORTFOLIO, currency=DEMO_CURRENCY)
        db.add(portfolio)
        db.flush()
    return portfolio


def site_for_new_asset(db: Session, *, name: str, edge_id: str | None,
                       latitude: float | None, longitude: float | None) -> Site:
    """The site a new MVP asset belongs to. Added to the session, not committed."""
    if edge_id is not None:
        # Edge IDs are only unique within one Backend, so an MVP asset can only
        # join the edge's site while that site has no connection yet
        site = (
            db.query(Site)
            .filter(Site.source_connection_id.is_(None), Site.edge_id == edge_id)
            .first()
        )
        if site is not None:
            return site
        site_name = edge_id
    else:
        site_name = name

    site = Site(
        portfolio=demo_portfolio(db),
        name=site_name,
        edge_id=edge_id,
        timezone=MVP_TIMEZONE,
        latitude=latitude,
        longitude=longitude,
    )
    db.add(site)
    # Sent now, so the next asset on the same edge finds it even with autoflush off
    db.flush()
    return site
