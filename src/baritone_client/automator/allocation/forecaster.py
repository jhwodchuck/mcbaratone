"""
Resource Forecaster Module
"""

import threading
import statistics
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict

from ...models.models import ResourceForecast
from ..resource_manager import ResourceManager


class ResourceForecaster:
    """
    Intelligent resource forecasting based on usage patterns and mission planning.

    Implements:
    - Historical usage analysis
    - Mission pattern recognition
    - Predictive demand modeling
    - Confidence-based forecasting
    """

    def __init__(self, resource_manager: ResourceManager):
        """
        Initialize resource forecaster.

        Args:
            resource_manager: Resource manager for historical data
        """
        self.resource_manager = resource_manager
        self.usage_history: Dict[str, List[Tuple[datetime, int]]] = defaultdict(list)
        self.mission_patterns: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()

    def generate_forecast(
        self,
        resource_id: str,
        forecast_horizon: int = 60,  # minutes
        confidence_threshold: float = 0.7
    ) -> Optional[ResourceForecast]:
        """
        Generate resource forecast for specified horizon.

        Args:
            resource_id: Resource to forecast
            forecast_horizon: Minutes into future
            confidence_threshold: Minimum confidence level

        Returns:
            Forecast if confidence meets threshold, None otherwise
        """
        with self._lock:
            if resource_id not in self.usage_history or len(self.usage_history[resource_id]) < 3:
                return None

            # Analyze historical usage patterns
            historical_usage = self.usage_history[resource_id]
            if not historical_usage:
                return None

            # Calculate trend and seasonality
            predicted_demand = self._calculate_predicted_demand(resource_id, forecast_horizon)
            predicted_supply = self._calculate_predicted_supply(resource_id, forecast_horizon)
            confidence = self._calculate_confidence(resource_id, predicted_demand)

            if confidence < confidence_threshold:
                return None

            # Identify influencing factors
            factors = self._identify_influencing_factors(resource_id)

            return ResourceForecast(
                resource_id=resource_id,
                timestamp=datetime.now(),
                predicted_demand=predicted_demand,
                predicted_supply=predicted_supply,
                confidence_level=confidence,
                forecast_horizon=forecast_horizon,
                influencing_factors=factors
            )

    def record_usage(self, resource_id: str, quantity: int, mission_id: Optional[str] = None):
        """
        Record resource usage for pattern analysis.

        Args:
            resource_id: Resource used
            quantity: Quantity used
            mission_id: Mission that used the resource
        """
        with self._lock:
            timestamp = datetime.now()
            self.usage_history[resource_id].append((timestamp, quantity))

            # Keep only recent history (last 24 hours)
            cutoff = datetime.now() - timedelta(hours=24)
            self.usage_history[resource_id] = [
                (ts, qty) for ts, qty in self.usage_history[resource_id]
                if ts > cutoff
            ]

            # Update mission patterns
            if mission_id:
                if mission_id not in self.mission_patterns:
                    self.mission_patterns[mission_id] = {
                        'resources': defaultdict(list),
                        'start_time': timestamp,
                        'end_time': None
                    }
                self.mission_patterns[mission_id]['resources'][resource_id].append((timestamp, quantity))

    def _calculate_predicted_demand(self, resource_id: str, horizon: int) -> int:
        """Calculate predicted demand using time series analysis."""
        history = self.usage_history[resource_id]
        if len(history) < 2:
            return 0

        # Simple exponential smoothing for prediction
        recent_usage = [qty for _, qty in history[-10:]]  # Last 10 data points
        if not recent_usage:
            return 0

        # Calculate trend
        if len(recent_usage) >= 2:
            trend = (recent_usage[-1] - recent_usage[0]) / len(recent_usage)
            predicted = recent_usage[-1] + (trend * (horizon / 60))  # Scale to horizon
            return max(0, int(predicted))

        return int(statistics.mean(recent_usage))

    def _calculate_predicted_supply(self, resource_id: str, horizon: int) -> int:
        """Calculate predicted supply based on current inventory trends."""
        current_inventory = self.resource_manager.get_item_count(resource_id)

        # Simple model: assume inventory stays constant unless historical trends show otherwise
        history = self.usage_history[resource_id]
        if len(history) >= 5:
            # Check if inventory is trending up or down
            recent_trend = self._calculate_inventory_trend(resource_id)
            predicted_inventory = current_inventory + (recent_trend * (horizon / 60))
            return max(0, int(predicted_inventory))

        return current_inventory

    def _calculate_inventory_trend(self, resource_id: str) -> float:
        """Calculate inventory trend per minute."""
        history = self.usage_history[resource_id]
        if len(history) < 2:
            return 0.0

        # Calculate rate of change
        time_diffs = []
        quantity_diffs = []

        for i in range(1, len(history)):
            time_diff = (history[i][0] - history[i-1][0]).total_seconds() / 60  # minutes
            quantity_diff = history[i][1] - history[i-1][1]
            if time_diff > 0:
                time_diffs.append(time_diff)
                quantity_diffs.append(quantity_diff)

        if not time_diffs:
            return 0.0

        # Weighted average rate of change
        total_weight = sum(time_diffs)
        weighted_rate = sum(qd * td for qd, td in zip(quantity_diffs, time_diffs)) / total_weight

        return weighted_rate

    def _calculate_confidence(self, resource_id: str, predicted_demand: int) -> float:
        """Calculate confidence level for prediction."""
        history = self.usage_history[resource_id]
        if len(history) < 3:
            return 0.0

        # Calculate coefficient of variation
        quantities = [qty for _, qty in history]
        if len(quantities) < 2:
            return 0.0

        mean_usage = statistics.mean(quantities)
        if mean_usage == 0:
            return 0.0

        std_dev = statistics.stdev(quantities)
        cv = std_dev / mean_usage  # Coefficient of variation

        # Higher variation = lower confidence
        confidence = max(0.0, 1.0 - cv)

        # Boost confidence with more data points
        data_factor = min(1.0, len(history) / 20.0)  # Max confidence at 20+ data points
        confidence *= data_factor

        return confidence

    def _identify_influencing_factors(self, resource_id: str) -> List[str]:
        """Identify factors influencing resource usage."""
        factors = []

        # Check mission patterns
        active_missions = [
            mission_id for mission_id, pattern in self.mission_patterns.items()
            if pattern.get('end_time') is None and resource_id in pattern['resources']
        ]

        if active_missions:
            factors.extend([f"mission_{mid}" for mid in active_missions])

        # Check time-based patterns
        history = self.usage_history[resource_id]
        if len(history) >= 10:
            # Simple peak detection
            quantities = [qty for _, qty in history]
            mean_qty = statistics.mean(quantities)
            max_qty = max(quantities)

            if max_qty > mean_qty * 2:
                factors.append("peak_usage_pattern")

        return factors
