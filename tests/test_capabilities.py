"""`fetch_mosaic_schema` reads Mosaic's `mosaicSchema` GraphQL root field (platform ADR-0007 Q3,
renamed from `hippoSchema` in Mosaic v0.16.0)."""
from reel.planner import capabilities


def test_fetch_mosaic_schema_queries_and_reads_mosaic_schema(monkeypatch):
    seen = {}

    def fake_graphql_query(endpoint, query):
        seen["endpoint"], seen["query"] = endpoint, query
        return {
            "mosaicSchema": [
                {
                    "name": "Donor",
                    "accessorName": "donors",
                    "fields": [{"name": "donor_id", "kind": "SCALAR"}],
                }
            ]
        }

    monkeypatch.setattr(capabilities, "graphql_query", fake_graphql_query)
    schema = capabilities.fetch_mosaic_schema("http://mosaic/graphql")

    assert seen["endpoint"] == "http://mosaic/graphql"
    assert "mosaicSchema" in seen["query"]
    assert "hippoSchema" not in seen["query"]
    assert schema == {
        "Donor": {
            "accessor_name": "donors",
            "fields": {"donor_id": {"name": "donor_id", "kind": "SCALAR"}},
        }
    }
