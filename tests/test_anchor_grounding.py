"""The planner must not be steered onto an abstract anchor (reel#10).

On the brain bank schema `Sample` is abstract: its records live in the
collections of 33 concrete subclasses, and `anchor: "Sample"` is rejected with
UNKNOWN_ANCHOR. Two things pointed the model there:

1. `SPEC_TOOL`'s `anchor` description carried the example ``"Sample"``. The
   tool schema is the same for every deployment; an example that is concrete
   on one schema is an un-anchorable class on another.
2. The grounding carried no hierarchy, so nothing tied `Blood`, `Brain` and
   `CSF` to the word "sample" the user uses.

The fixtures here are capability manifests in the shape `mosaic://capabilities`
serves, with and without the hierarchy keys Mosaic ADR-0014 adds to each
entity (`abstract`, `parent`, `kinds`). A manifest without them must render
exactly as it did before.
"""
import re

import pytest

from reel.planner.spec_planner import (
    SPEC_TOOL,
    build_grounding_context,
    render_capability_grounding,
    render_entity_kinds,
    render_entity_kinds_section,
    render_traversable_edges,
)
from reel.story.turn import TURN_TOOL, build_turn_grounding


def _entity(name, description, *, fields=(), **hierarchy):
    return {
        "name": name, "accessor_name": name.lower() + "s", "description": description,
        "search_available": False, "fields": list(fields), **hierarchy,
    }


def _scalar(name):
    return {
        "name": name, "kind": "scalar", "range": "string", "required": False,
        "multivalued": False, "enum_values": [], "target_entity_type": None,
        "filter_ops": ["eq", "neq", "in", "contains", "is_null"],
        "predicate": False, "orderable": True,
    }


DONOR_REF = {
    "name": "donor", "kind": "reference", "range": "Donor", "required": True,
    "multivalued": False, "enum_values": [], "target_entity_type": "Donor",
    "filter_ops": [], "predicate": True, "orderable": False,
}

#: What a manifest looks like today: only concrete, exposed classes, no hierarchy.
FLAT = {
    "Blood": _entity("Blood", "A blood sample.", fields=[_scalar("tube_type"), DONOR_REF]),
    "Brain": _entity("Brain", "A whole-brain sample.", fields=[_scalar("hemisphere"), DONOR_REF]),
    "CSF": _entity("CSF", "A cerebrospinal fluid sample.", fields=[DONOR_REF]),
    "Donor": _entity("Donor", "A tissue donor.", fields=[_scalar("sex")]),
}

#: Hierarchy via `parent` only, the family itself NOT in the manifest (abstract,
#: unexposed -- the pre-ADR-0014 brain bank situation, with the hierarchy added).
PARENT_ONLY = {
    "Blood": _entity("Blood", "A blood sample.", fields=[DONOR_REF], parent="Sample"),
    "Brain": _entity("Brain", "A whole-brain sample.", fields=[DONOR_REF], parent="Sample"),
    "CSF": _entity("CSF", "A cerebrospinal fluid sample.", fields=[DONOR_REF], parent="Sample"),
    "Donor": _entity("Donor", "A tissue donor.", fields=[_scalar("sex")], parent=None),
}

#: ADR-0014's full shape: the abstract family is itself a manifest entry (and so a
#: legal anchor whose collection spans its closure), with `abstract`, `kinds` and
#: `parent` on every entity.
ADR_0014 = {
    "Sample": _entity("Sample", "A biological specimen.", fields=[DONOR_REF],
                      abstract=True, parent="NamedThing", kinds=["Blood", "Brain", "CSF"]),
    "Blood": _entity("Blood", "A blood sample.", fields=[DONOR_REF],
                     abstract=False, parent="Sample", kinds=[]),
    "Brain": _entity("Brain", "A whole-brain sample.", fields=[DONOR_REF],
                     abstract=False, parent="Sample", kinds=[]),
    "CSF": _entity("CSF", "A cerebrospinal fluid sample.", fields=[DONOR_REF],
                   abstract=False, parent="Sample", kinds=[]),
    "Donor": _entity("Donor", "A tissue donor.", fields=[_scalar("sex")],
                     abstract=False, parent="NamedThing", kinds=[]),
}


class TestToolSchemaNamesNoClass:
    def test_anchor_description_names_no_schema_class(self):
        desc = SPEC_TOOL["function"]["parameters"]["properties"]["anchor"]["description"]
        assert "Sample" not in desc
        # No quoted, capitalised token at all -- the shape an example class name takes.
        assert re.search(r'"[A-Z]\w*"', desc) is None, desc
        assert "e.g." not in desc

    def test_anchor_description_still_states_the_format(self):
        desc = SPEC_TOOL["function"]["parameters"]["properties"]["anchor"]["description"]
        assert "singular" in desc and "capitalised" in desc
        assert "plural accessor" in desc
        assert "grounding" in desc

    def test_turn_tool_inherits_the_same_anchor_description(self):
        turn_anchor = TURN_TOOL["function"]["parameters"]["properties"]["query_spec"]["properties"]["anchor"]
        assert turn_anchor == SPEC_TOOL["function"]["parameters"]["properties"]["anchor"]


class TestAManifestWithoutAHierarchyRendersNothingNew:
    def test_no_kinds_section(self):
        assert render_entity_kinds(FLAT) == ""
        assert render_entity_kinds_section(FLAT) == ""

    def test_the_grounding_is_unchanged_in_shape(self):
        text = build_grounding_context(FLAT)
        assert "Kinds of entities" not in text
        assert "kind of" not in text and "abstract" not in text
        # The kinds placeholder leaves no trace -- not even a blank line -- between
        # the edges section and the hard constraints.
        edges = render_traversable_edges(FLAT)
        assert edges + "\n\n## Hard constraints" in text
        assert "{{" not in text

    def test_entity_headers_are_unchanged(self):
        text = render_capability_grounding(FLAT)
        assert '- entity "Blood": A blood sample.' in text
        assert '- entity "Donor": A tissue donor.' in text

    def test_a_null_parent_is_no_hierarchy(self):
        caps = {"Donor": {**FLAT["Donor"], "parent": None, "kinds": []}}
        assert render_entity_kinds(caps) == ""
        assert '- entity "Donor": A tissue donor.' in render_capability_grounding(caps)

    def test_the_turn_grounding_is_unchanged_too(self):
        text = build_turn_grounding(FLAT, None, ())
        assert "Kinds of entities" not in text and "{{" not in text


class TestAFamilyThatIsNotInTheManifestIsNotAnAnchor:
    """`parent` names a class with no manifest entry: it has no collection, so
    the model must pick a kind or ask."""

    def test_kinds_are_derived_from_parent_links(self):
        text = render_entity_kinds(PARENT_ONLY)
        assert text.count("\n") == 0  # one family, one line
        assert '"Sample" is a family with NO collection of its own' in text
        assert "NOT a legal anchor" in text
        assert 'Its kinds are: "Blood", "Brain", "CSF".' in text

    def test_the_model_is_told_to_pick_a_kind_or_ask(self):
        text = render_entity_kinds(PARENT_ONLY)
        assert "Anchor on the kind the user means" in text
        assert "ask a clarification" in text

    def test_each_kind_is_tied_to_its_family_in_the_listing(self):
        # The word "sample" now appears next to Blood/Brain/CSF -- the vocabulary the
        # user and the schema descriptions use, which the flat listing lacked.
        text = render_capability_grounding(PARENT_ONLY)
        assert '- entity "Blood" (a kind of Sample): A blood sample.' in text
        assert '- entity "CSF" (a kind of Sample):' in text
        assert '- entity "Donor": A tissue donor.' in text  # parent=None: no annotation

    def test_the_section_sits_between_edges_and_hard_constraints(self):
        text = build_grounding_context(PARENT_ONLY)
        edges = render_traversable_edges(PARENT_ONLY)
        assert edges + "\n\n## Kinds of entities" in text
        assert text.index("## Kinds of entities") < text.index("## Hard constraints")
        assert "{{" not in text

    def test_the_turn_grounding_carries_the_section(self):
        text = build_turn_grounding(PARENT_ONLY, None, ())
        assert "## Kinds of entities" in text
        assert '"Sample" is a family with NO collection of its own' in text
        assert text.index("## Kinds of entities") < text.index("## Conversation so far")


class TestAFamilyInTheManifestIsAnAnchorWithKinds:
    """ADR-0014's shape: the abstract class has an entry, so its collection
    exists (it spans the closure) and the section is a refinement, not a
    workaround."""

    def test_the_family_is_offered_as_an_anchor(self):
        (line,) = [l for l in render_entity_kinds(ADR_0014).splitlines() if l.startswith('- "Sample"')]
        assert '"Sample" is queryable itself and has more specific kinds: "Blood", "Brain", "CSF".' in line
        assert "NOT a legal anchor" not in line
        assert 'Anchor on "Sample" when the question is about the family as a whole' in line

    def test_kinds_come_from_the_kinds_key_merged_with_parent_links(self):
        caps = {
            "Sample": _entity("Sample", "x", abstract=True, kinds=["Blood"]),
            "Blood": _entity("Blood", "x", parent="Sample"),
            "CSF": _entity("CSF", "x", parent="Sample"),  # not in Sample.kinds, still a kind
        }
        assert 'kinds: "Blood", "CSF".' in render_entity_kinds(caps)

    def test_a_leaf_with_empty_kinds_is_not_a_family(self):
        text = render_entity_kinds(ADR_0014)
        assert '"Blood" is' not in text and '"Donor" is' not in text

    def test_an_unexposed_root_still_renders_as_not_an_anchor(self):
        # NamedThing is every entity's ancestor and is not in the manifest: the model
        # is told not to anchor on it, same as any other absent family.
        text = render_entity_kinds(ADR_0014)
        assert '"NamedThing" is a family with NO collection of its own' in text
        assert 'Its kinds are: "Donor", "Sample".' in text

    def test_families_are_listed_in_name_order(self):
        text = render_entity_kinds(ADR_0014)
        assert text.index('"NamedThing"') < text.index('"Sample"')

    def test_the_abstract_entry_is_marked_in_the_listing(self):
        text = render_capability_grounding(ADR_0014)
        assert '- entity "Sample" (abstract family; see "Kinds of entities" below): A biological specimen.' in text
        assert '- entity "Blood" (a kind of Sample): A blood sample.' in text

    def test_the_abstract_entrys_edges_are_still_offered(self):
        # Under ADR-0014 an abstract anchor is real; its traversable edges are too.
        assert 'on anchor "Sample": edge="donor" reaches Donor' in render_traversable_edges(ADR_0014)

    def test_a_concrete_parent_with_kinds_reads_the_same_way(self):
        caps = {
            "Dataset": _entity("Dataset", "x", abstract=False, parent=None, kinds=["ArrayDataset"]),
            "ArrayDataset": _entity("ArrayDataset", "x", abstract=False, parent="Dataset", kinds=[]),
        }
        text = render_entity_kinds(caps)
        assert '"Dataset" is queryable itself and has more specific kinds: "ArrayDataset".' in text


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
