"""Composition root for the trusted transportation capability."""

from collections.abc import Sequence

from rockygpt_brain.capabilities.base import CapabilityRun, ConversationMessage
from rockygpt_brain.capabilities.transportation import (
    execution,
    interpretation,
    renderer,
    repository,
)


class TransportationCapability:
    """Interpret and deterministically execute only campus transportation requests."""

    name = "transportation"

    def run(
        self, messages: Sequence[ConversationMessage], model: str
    ) -> CapabilityRun:
        answer, interpreted = interpretation.interpret_transportation(messages, model)
        result = None
        provenance = None
        if interpreted.selected:
            request = interpreted.request
            assert request is not None
            trusted_data = None
            if getattr(request, "kind", None) in {"query", "comparison"}:
                trusted_data = repository.load_trusted_shuttle_data()
                if not execution.route_mentions_match_trusted_data(request, trusted_data):
                    _, interpreted = interpretation.repair_transportation_interpretation(
                        messages, model
                    )
                    request = interpreted.request
                    assert request is not None
                    if (
                        getattr(request, "kind", None) in {"query", "comparison"}
                        and not execution.route_mentions_match_trusted_data(
                            request, trusted_data
                        )
                    ):
                        _, interpreted = interpretation.interpretation_failure(
                            interpreted.model
                        )
                        request = interpreted.request
                        assert request is not None
            result = execution.execute_transportation(request, data=trusted_data)
            provenance = result.provenance
            answer = renderer.answer_transportation(result)

        return CapabilityRun(
            selected=interpreted.selected,
            answer=answer,
            model=interpreted.model,
            inspection={
                "transportationInterpretation": interpreted.model_dump(mode="json"),
                "transportationResult": (
                    result.model_dump(mode="json") if result is not None else None
                ),
                "transportationProvenance": (
                    provenance.model_dump(mode="json")
                    if provenance is not None
                    else None
                ),
            },
        )


CAPABILITY = TransportationCapability()
