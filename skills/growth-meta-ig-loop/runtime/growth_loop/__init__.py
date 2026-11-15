"""Resumable marketing workflows; external execution never implied by a plan."""

__version__ = "0.13.0a2"

from .commercial_contract import (
    compileCommercialOffer, rankCommercialActions, designCommercialExperiment,
    validateCommercialMethod, decideCommercialTransfer,
    estimateCommercialActionValues,
)
