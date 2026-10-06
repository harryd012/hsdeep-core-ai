# ---
# Endpoint-contract tests are deliberately in their own module: they are
# synchronous (pure route introspection), and a module-wide
# @pytest.mark.asyncio would mislabel them and emit warnings.
# ---

class TestEndpointContract:
    """Route wiring + the "client cannot choose a tenant" guarantee."""

    def test_route_is_registered(self):
        from fastapi.routing import APIRoute

        from app.main import app

        paths = [r.path for r in app.routes if isinstance(r, APIRoute)]
        assert "/api/dashboard/operational-intelligence" in paths

    def test_endpoint_exposes_no_client_selectable_tenant_parameter(self):
        """`?tenant_id=` must NOT be an accepted parameter on this route.

        If a client-supplied tenant_id is ever added to the signature, a caller
        could read another tenant's estate. Asserted against FastAPI's real
        route dependant - the object that actually parses requests - so an
        alias or a reordered default cannot hide it.
        """
        from fastapi.routing import APIRoute

        from app.main import app

        route = next(
            r
            for r in app.routes
            if isinstance(r, APIRoute)
            and r.path == "/api/dashboard/operational-intelligence"
        )

        query_names = {p.name for p in route.dependant.query_params} | {
            p.alias for p in route.dependant.query_params
        }
        assert "tenant_id" not in query_names, (
            f"route must not accept a client-supplied tenant_id; got {query_names}"
        )
        assert query_names == set(), f"expected no query params, got {query_names}"
