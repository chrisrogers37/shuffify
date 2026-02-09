from typing import List, Dict, Any, Optional
from . import ShuffleAlgorithm


class TempoGradientShuffle(ShuffleAlgorithm):
    """Sort tracks by tempo (BPM) for smooth DJ-style transitions."""

    @property
    def name(self) -> str:
        return "Tempo Gradient"

    @property
    def description(self) -> str:
        return (
            "Sorts tracks by tempo (BPM) for smooth DJ-style transitions. "
            "Choose ascending for a building energy flow, or descending to wind down."
        )

    @property
    def parameters(self) -> dict:
        return {
            "direction": {
                "type": "string",
                "description": "Sort direction for tempo",
                "default": "ascending",
                "options": ["ascending", "descending"],
            }
        }

    @property
    def requires_features(self) -> bool:
        return True

    def shuffle(
        self,
        tracks: List[Dict[str, Any]],
        features: Optional[Dict[str, Dict[str, Any]]] = None,
        **kwargs,
    ) -> List[str]:
        """
        Sort tracks by BPM for smooth transitions.

        Args:
            tracks: List of track dictionaries from Spotify API.
            features: Dictionary mapping track IDs to audio features.
                      Each feature dict should have a 'tempo' key.
            **kwargs: Additional parameters.
                - direction: "ascending" or "descending" (default "ascending").

        Returns:
            List of track URIs sorted by tempo.
        """
        direction = kwargs.get("direction", "ascending")
        features = features or {}

        uris = [t["uri"] for t in tracks if t.get("uri")]
        if len(uris) <= 1:
            return uris

        # Build a mapping from URI to tempo
        default_tempo = 120.0  # Fallback for tracks without features
        uri_to_tempo = {}
        for t in tracks:
            uri = t.get("uri")
            if not uri:
                continue
            track_id = t.get("id", "")
            tempo = default_tempo
            # Check features by track ID
            if track_id and track_id in features:
                feat = features[track_id]
                if isinstance(feat, dict):
                    tempo = feat.get("tempo", default_tempo)
            # Also check by URI as a fallback
            elif uri in features:
                feat = features[uri]
                if isinstance(feat, dict):
                    tempo = feat.get("tempo", default_tempo)
            uri_to_tempo[uri] = tempo

        reverse = direction == "descending"
        sorted_uris = sorted(uris, key=lambda u: uri_to_tempo.get(u, default_tempo), reverse=reverse)

        return sorted_uris
