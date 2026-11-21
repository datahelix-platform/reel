"""Live schema grounding: mosaicSchema + the capability manifest, fetched fresh every run.

Never cache these across sessions and never assume field names from memory. mosaic#149 (now
fixed, PR#150) was exactly this hazard: a plausible-looking guess silently produced a
wrong-but-valid-shaped answer. Unknown names are loud now, but the discipline stands -- resolve
names from the live schema rather than guessing a transformation.
"""
import json
import urllib.request


def graphql_query(endpoint: str, query: str) -> dict:
    payload = {"query": query}
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        result = json.load(resp)
    if "errors" in result:
        raise RuntimeError(f"GraphQL error: {result['errors']}")
    return result["data"]


MOSAIC_SCHEMA_QUERY = """
{ mosaicSchema { name accessorName description fields { name kind range role required
  multivalued identifier description targetEntityType enumName enumValues }
  relationships { field targetEntityType } } }
"""


def fetch_mosaic_schema(endpoint: str) -> dict:
    """{entity_name: {accessor_name, fields: {slot_name: field_info}}}.

    The source of truth for field names. Since mosaic#149/PR#150 the server accepts both the
    slot name and its camelCase spelling, but names are still resolved from here rather than
    guessed -- and mosaicSchema also carries the kind/multivalued metadata the validator needs
    to reject unfilterable fields before execution.
    """
    data = graphql_query(endpoint, MOSAIC_SCHEMA_QUERY)
    schema = {}
    for entity in data["mosaicSchema"]:
        schema[entity["name"]] = {
            "accessor_name": entity["accessorName"],
            "fields": {f["name"]: f for f in entity["fields"]},
        }
    return schema


def load_capability_manifest(path: str) -> dict:
    with open(path) as f:
        return json.load(f)
