"""Tests d'acceptance — chantier Évaluation & MLOps."""

from __future__ import annotations

import pytest
from conftest import build_degraded_agent, build_reference_agent

from velmo.mlops import (
    DeliveryBlocked,
    current_version,
    enforce_threshold,
    run_eval,
    write_report,
)


def test_scores_produced_and_versioned():
    # Critère : note globale + notes mémoire / garde-fous / qualité, versionnées.
    scores = run_eval(build_reference_agent())
    assert scores.global_ is not None and 0.0 <= scores.global_ <= 1.0
    assert scores.memory is not None
    assert scores.guardrails is not None
    assert scores.quality is not None
    assert current_version()


def test_regression_blocks_delivery():
    # Critère : une régression fait chuter la note et bloque la livraison.
    good = run_eval(build_reference_agent())
    degraded = run_eval(build_degraded_agent())

    assert degraded.global_ < good.global_
    enforce_threshold(good, 0.8)  # ne doit pas lever
    with pytest.raises(DeliveryBlocked):
        enforce_threshold(degraded, 0.8)


def test_report_contains_signals(tmp_path):
    # Critère : note mémoire, taux de blocage, taux de faux positifs, latence, coût visibles.
    scores = run_eval(build_reference_agent())
    report = tmp_path / "report.md"
    write_report(scores, report)

    text = report.read_text(encoding="utf-8").lower()
    for signal in ["memoire", "blocage", "faux positif", "latence", "cout"]:
        assert signal in text
