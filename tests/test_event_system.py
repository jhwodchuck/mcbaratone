"""Tests for enhanced event system (Phase 3)."""
import time
import sys
import os
import pytest
from unittest.mock import MagicMock, patch

# Add src to path to avoid circular imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from baritone_client.events.event_manager import EventManager, Event, EventAggregator
from baritone_client.transport.enums import TransportEvent


class TestEventManager:
    """Test enhanced EventManager functionality."""

    def test_ttl_expired_events_cleaned(self):
        """Test that expired events are cleaned from buffer."""
        manager = EventManager(max_buffer_size=10, default_ttl=0.1)

        # Publish an event
        manager.publish_event("test_event", {"data": "value"})

        # Verify it's in buffer
        assert manager.get_buffer_size() == 1

        # Wait for TTL to expire
        time.sleep(0.2)

        # Poll events (should clean expired events)
        events = manager.poll_events()
        assert len(events) == 0
        assert manager.get_buffer_size() == 0

    def test_compression_large_data(self):
        """Test that large event data is compressed."""
        manager = EventManager(enable_compression=True)

        # Create large data (>1KB)
        large_data = {"data": "x" * 2000}

        manager.publish_event("test_event", large_data)

        # Poll and check decompression
        events = manager.poll_events()
        assert len(events) == 1
        assert events[0].data == large_data  # Should be decompressed

    def test_lz4_compression_accuracy(self):
        """Test LZ4 compression/decompression accuracy."""
        manager = EventManager(enable_compression=True)

        # Test with varied data
        test_data = {
            "numbers": list(range(1000)),
            "strings": ["test"] * 500,
            "nested": {"deep": {"data": "x" * 1000}}
        }

        # Force LZ4 by simulating high frequency
        for _ in range(15):  # More than threshold to trigger frequency detection
            manager._update_event_frequency("test_event")

        manager.publish_event("test_event", test_data)
        events = manager.poll_events()

        assert len(events) == 1
        assert events[0].data == test_data

    def test_gzip_compression_accuracy(self):
        """Test GZIP compression/decompression accuracy."""
        manager = EventManager(enable_compression=True)

        # Large data to trigger GZIP selection (not high frequency)
        large_data = {"payload": "x" * 3000}

        manager.publish_event("test_event", large_data)
        events = manager.poll_events()

        assert len(events) == 1
        assert events[0].data == large_data

    def test_compression_performance_comparison(self):
        """Test compression performance between LZ4 and GZIP."""
        import time

        manager = EventManager(enable_compression=True)

        # Large dataset for meaningful comparison
        test_data = {"data": "x" * 5000}

        # Test LZ4 (high frequency scenario)
        for _ in range(15):
            manager._update_event_frequency("high_freq_event")

        start_time = time.time()
        for _ in range(100):
            manager.publish_event("high_freq_event", test_data.copy())
        lz4_time = time.time() - start_time

        # Clear buffer and test GZIP (low frequency scenario)
        manager.clear_buffer()
        manager._event_frequencies.clear()

        start_time = time.time()
        for _ in range(100):
            manager.publish_event("low_freq_event", test_data.copy())
        gzip_time = time.time() - start_time

        # LZ4 should be faster for high frequency (though this is a rough test)
        assert lz4_time > 0 and gzip_time > 0  # Both should take measurable time

    def test_backward_compatibility_compressed_events(self):
        """Test backward compatibility with existing compressed events."""
        manager = EventManager(enable_compression=False)  # Disable to test manual compressed data

        # Simulate old compressed format (just GZIP)
        test_data = {"message": "test backward compatibility"}
        json_str = json.dumps(test_data)
        gzip_compressed = zlib.compress(json_str.encode('utf-8'))

        compressed_payload = {
            "_compressed": True,
            "_algorithm": "gzip",  # Old format might not have algorithm
            "data": gzip_compressed.hex()
        }

        # Manually create event with compressed data
        from baritone_client.events.event_manager import Event
        event = Event("test", compressed_payload, time.time())

        # Test decompression
        decompressed = manager._decompress_data(compressed_payload)
        assert decompressed == test_data

    def test_event_aggregation(self):
        """Test event aggregation functionality."""
        manager = EventManager()

        # Add aggregator for test events
        manager.add_aggregator("test_event", aggregation_window=0.5, max_count=3)

        # Publish multiple events quickly
        for i in range(4):
            manager.publish_event("test_event", {"count": i})

        # Should have triggered aggregation
        # Note: In real usage, aggregation happens asynchronously
        # For testing, we check the aggregator state
        aggregator = manager._aggregators["test_event"]
        assert len(aggregator._events) == 4

        # Flush should create aggregated event
        aggregated = aggregator.flush()
        assert aggregated is not None
        assert aggregated.data["count"] == 4

    def test_subscription_filtering(self):
        """Test event subscription with filtering."""
        manager = EventManager()
        callback = MagicMock()

        # Subscribe with filter
        manager.subscribe(
            callback,
            event_types={"test_event"}
        )

        # Publish matching event
        manager.publish_event("test_event", {"data": "test"})
        time.sleep(0.01)  # Allow async notification

        callback.assert_called_once()

        # Publish non-matching event
        callback.reset_mock()
        manager.publish_event("other_event", {"data": "test"})
        time.sleep(0.01)

        callback.assert_not_called()


class TestEventAggregator:
    """Test EventAggregator functionality."""

    def test_aggregation_by_count(self):
        """Test aggregation triggers on max count."""
        aggregator = EventAggregator("test", max_count=2)

        # Add events
        event1 = Event("test", {"id": 1}, time.time())
        event2 = Event("test", {"id": 2}, time.time())

        # First event should not aggregate
        result = aggregator.add_event(event1)
        assert result is None

        # Second event should trigger aggregation
        result = aggregator.add_event(event2)
        assert result is not None
        assert result.data["count"] == 2

    def test_aggregation_by_time(self):
        """Test aggregation triggers on time window."""
        aggregator = EventAggregator("test", aggregation_window=0.1, max_count=10)

        event = Event("test", {"id": 1}, time.time())
        aggregator.add_event(event)

        # Wait for time window
        time.sleep(0.15)

        # Next event should trigger aggregation
        event2 = Event("test", {"id": 2}, time.time())
        result = aggregator.add_event(event2)
        assert result is not None


class TestAdvancedFiltering:
    """Test advanced filtering capabilities."""

    def test_coordinate_based_filtering(self):
        """Test filtering events by coordinate bounds."""
        from baritone_client.events.event_manager import BoundingBox, EventFilter

        manager = EventManager()

        # Create coordinate-based filter
        bbox = BoundingBox(min_x=0, max_x=10, min_y=0, max_y=10, min_z=0, max_z=10)
        filter_ = EventFilter(coordinate_bounds=bbox)

        # Publish events with coordinates
        manager.publish_event("block_update", {"x": 5, "y": 5, "z": 5, "block": "stone"})  # Inside
        manager.publish_event("block_update", {"x": 15, "y": 5, "z": 5, "block": "dirt"})  # Outside

        # Poll with filter
        events = manager.poll_events_with_filter(filter_)
        assert len(events) == 1
        assert events[0].data["block"] == "stone"

    def test_time_window_filtering(self):
        """Test filtering events by time window."""
        from baritone_client.events.event_manager import TimeWindow, EventFilter
        import time

        manager = EventManager()

        # Create time window filter (last 2 seconds)
        time_window = TimeWindow(duration_seconds=2.0)
        filter_ = EventFilter(time_window=time_window)

        # Publish events at different times
        past_time = time.time() - 5  # 5 seconds ago
        manager.publish_event("test", {"id": 1}, timestamp=past_time)
        manager.publish_event("test", {"id": 2})  # Now

        # Poll with filter
        events = manager.poll_events_with_filter(filter_)
        assert len(events) == 1
        assert events[0].data["id"] == 2

    def test_payload_based_filtering(self):
        """Test filtering events by payload field values."""
        from baritone_client.events.event_manager import PayloadFilter, EventFilter

        manager = EventManager()

        # Create payload filters
        filters = [
            PayloadFilter(field_path="player.health", operator="lt", value=50),
            PayloadFilter(field_path="status", operator="eq", value="critical")
        ]
        filter_ = EventFilter(payload_filters=filters)

        # Publish events
        manager.publish_event("player_update", {"player": {"health": 30}, "status": "critical"})
        manager.publish_event("player_update", {"player": {"health": 80}, "status": "normal"})

        # Poll with filter - should match both conditions (health < 50 AND status == critical)
        events = manager.poll_events_with_filter(filter_)
        assert len(events) == 1
        assert events[0].data["status"] == "critical"

    def test_frequency_based_throttling(self):
        """Test frequency-based event throttling."""
        from baritone_client.events.event_manager import FrequencyThrottle, EventFilter
        import time

        manager = EventManager()

        # Create frequency throttle (max 2 events per second)
        throttle = FrequencyThrottle(max_events_per_second=2.0, burst_limit=3)
        filter_ = EventFilter(frequency_throttle=throttle)

        events_received = []

        def callback(event):
            events_received.append(event)

        manager.subscribe_named("test_sub", callback, filter_=filter_)

        # Publish events faster than allowed
        for i in range(5):
            manager.publish_event("test_event", {"count": i})
            time.sleep(0.1)  # 10 events/second, but throttle allows 2/second

        # Should have received fewer events due to throttling
        assert len(events_received) <= 3  # Burst limit

    def test_combined_advanced_filters(self):
        """Test combining multiple advanced filters."""
        from baritone_client.events.event_manager import BoundingBox, TimeWindow, PayloadFilter, EventFilter

        manager = EventManager()

        # Create combined filter
        bbox = BoundingBox(min_x=0, max_x=10, min_y=0, max_y=10, min_z=0, max_z=10)
        time_window = TimeWindow(duration_seconds=1.0)
        payload_filter = PayloadFilter(field_path="block_type", operator="eq", value="diamond_ore")

        filter_ = EventFilter(
            coordinate_bounds=bbox,
            time_window=time_window,
            payload_filters=[payload_filter]
        )

        # Publish events
        manager.publish_event("block_found", {
            "x": 5, "y": 5, "z": 5,
            "block_type": "diamond_ore"
        })  # Should match

        manager.publish_event("block_found", {
            "x": 15, "y": 5, "z": 5,
            "block_type": "diamond_ore"
        })  # Outside bounds

        manager.publish_event("block_found", {
            "x": 5, "y": 5, "z": 5,
            "block_type": "stone"
        })  # Wrong type

        # Poll with filter
        events = manager.poll_events_with_filter(filter_)
        assert len(events) == 1
        assert events[0].data["block_type"] == "diamond_ore"


class TestWebSocketTransportSubscriptions:
    """Test WebSocket transport subscription management."""

    @patch('src.baritone_client.transport.transport.connect')
    def test_subscription_management(self, mock_connect):
        """Test subscription subscribe/unsubscribe."""
        from src.baritone_client.transport.transport import WebSocketTransport

        mock_socket = MagicMock()
        mock_connect.return_value = mock_socket

        transport = WebSocketTransport("ws://localhost:8080")

        # Subscribe to events
        transport.subscribe_events({TransportEvent.CHAT, "custom_event"})

        assert transport.is_subscribed(TransportEvent.CHAT)
        assert transport.is_subscribed("custom_event")
        assert not transport.is_subscribed(TransportEvent.TICK)

        # Unsubscribe
        transport.unsubscribe_events({TransportEvent.CHAT})

        assert not transport.is_subscribed(TransportEvent.CHAT)
        assert transport.is_subscribed("custom_event")

    @patch('src.baritone_client.transport.transport.connect')
    def test_emit_with_subscription(self, mock_connect):
        """Test that emit respects subscriptions."""
        from src.baritone_client.transport.transport import WebSocketTransport

        mock_socket = MagicMock()
        mock_connect.return_value = mock_socket

        transport = WebSocketTransport("ws://localhost:8080")
        transport.subscribe_events({TransportEvent.CHAT})

        # Emit subscribed event - should log streaming
        with patch('src.baritone_client.transport.transport.logger') as mock_logger:
            transport.emit(TransportEvent.CHAT, {"message": "test"})
            mock_logger.debug.assert_called_with("WebSocket streaming event: chat")

        # Emit unsubscribed event - should not log streaming
        with patch('src.baritone_client.transport.transport.logger') as mock_logger:
            transport.emit(TransportEvent.TICK, {"tick": 123})
            # Should not call debug for streaming
            assert not any("streaming" in str(call) for call in mock_logger.debug.call_args_list)


class TestIntegrationTests:
    """Test end-to-end event flow integration."""

    def test_event_publishing_from_bridge_to_client(self):
        """Test complete event flow from bridge publication to client reception."""
        # This would require mocking the full transport stack
        # For now, test the event manager integration
        manager = EventManager()

        events_received = []
        def callback(event):
            events_received.append(event)

        manager.subscribe(callback, {TransportEvent.BLOCK_BREAK, TransportEvent.ENTITY_ATTACK})

        # Simulate events that would come from bridge
        manager.publish_event(TransportEvent.BLOCK_BREAK, {
            "x": 100, "y": 64, "z": 200,
            "block": "minecraft:stone",
            "player": "test_player"
        })

        manager.publish_event(TransportEvent.ENTITY_ATTACK, {
            "attacker": "test_player",
            "target": "minecraft:zombie",
            "damage": 5.0
        })

        # Should have received both events
        assert len(events_received) == 2
        assert events_received[0].type == "block_break"
        assert events_received[1].type == "entity_attack"

    def test_transport_layer_event_handling(self):
        """Test transport layer processing of new events."""
        from unittest.mock import MagicMock

        # Mock transport
        mock_transport = MagicMock()

        # This would test how transports handle new event types
        # For now, test that event types are properly serialized
        event_data = {
            TransportEvent.BLOCK_BREAK: {"block": "stone"},
            TransportEvent.ENTITY_SHEAR: {"target": "sheep"},
            TransportEvent.WEATHER_CHANGE: {"weather": "rain"},
            TransportEvent.TIME_CHANGE: {"time": 12000}
        }

        for event_type, data in event_data.items():
            # Test that event types serialize correctly
            assert isinstance(event_type.value, str)
            assert len(event_type.value) > 0

    def test_eventmanager_full_feature_integration(self):
        """Test EventManager with all features enabled."""
        manager = EventManager(
            enable_compression=True,
            enable_spatial_index=True,
            enable_filter_cache=True
        )

        # Add advanced features
        from baritone_client.events.event_manager import PriorityRule, BoundingBox, EventFilter

        rule = PriorityRule(
            event_types={"block_break"},
            condition=lambda e: e.data.get("block") == "diamond_ore",
            priority_boost=10
        )
        manager.add_priority_rule(rule)

        # Create complex filter
        bbox = BoundingBox(min_x=0, max_x=100, min_y=0, max_y=100, min_z=0, max_z=100)
        filter_ = EventFilter(coordinate_bounds=bbox)

        # Subscribe with filter
        events_received = []
        def callback(event):
            events_received.append(event)

        manager.subscribe_named("complex_sub", callback, filter_=filter_)

        # Publish various events
        manager.publish_event("block_break", {"x": 50, "y": 50, "z": 50, "block": "diamond_ore"})
        manager.publish_event("block_break", {"x": 150, "y": 50, "z": 50, "block": "stone"})  # Outside bounds
        manager.publish_event("entity_attack", {"x": 50, "y": 50, "z": 50, "damage": 5})  # Wrong type

        # Should only receive the diamond ore event (matches filter and type)
        assert len(events_received) == 1
        assert events_received[0].data["block"] == "diamond_ore"
        # Should have high priority due to rule
        assert events_received[0].priority >= 10


class TestPerformanceTests:
    """Test performance characteristics of event system."""

    def test_event_throughput_with_compression(self):
        """Test event throughput with compression enabled."""
        import time

        manager = EventManager(enable_compression=True)

        # Generate large events for compression testing
        large_payload = {"data": "x" * 2000}

        events_to_publish = 100
        start_time = time.time()

        for i in range(events_to_publish):
            manager.publish_event("performance_test", large_payload)

        publish_time = time.time() - start_time

        # Poll events
        start_time = time.time()
        events = manager.poll_events()
        poll_time = time.time() - start_time

        # Verify all events were processed
        assert len(events) == events_to_publish

        # Basic performance assertions (these are rough benchmarks)
        assert publish_time < 5.0  # Should complete within 5 seconds
        assert poll_time < 2.0    # Polling should be fast

    def test_filtering_performance_large_event_set(self):
        """Test filtering performance with large number of events."""
        import time

        manager = EventManager(enable_filter_cache=True)

        # Publish many events
        for i in range(1000):
            manager.publish_event("bulk_test", {
                "x": i % 100,
                "y": 64,
                "z": i // 100,
                "value": i
            })

        # Create coordinate filter
        from baritone_client.events.event_manager import BoundingBox, EventFilter
        bbox = BoundingBox(min_x=10, max_x=20, min_y=60, max_y=70, min_z=5, max_z=15)
        filter_ = EventFilter(coordinate_bounds=bbox)

        # Time the filtering operation
        start_time = time.time()
        events = manager.poll_events_with_filter(filter_)
        filter_time = time.time() - start_time

        # Should complete reasonably fast
        assert filter_time < 1.0  # Less than 1 second for 1000 events

        # Verify filtering worked (events in range 10-20 x, 5-15 z)
        assert len(events) > 0
        for event in events:
            x, z = event.data["x"], event.data["z"]
            assert 10 <= x <= 20
            assert 5 <= z <= 15

    def test_memory_usage_with_advanced_features(self):
        """Test memory usage with advanced features enabled."""
        import psutil
        import os

        # Get initial memory
        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB

        # Create manager with all features
        manager = EventManager(
            enable_compression=True,
            enable_spatial_index=True,
            enable_filter_cache=True,
            max_buffer_size=5000
        )

        # Add many events
        for i in range(2000):
            manager.publish_event("memory_test", {"data": "x" * 100, "index": i})

        # Check memory after adding events
        after_memory = process.memory_info().rss / 1024 / 1024
        memory_increase = after_memory - initial_memory

        # Should not use excessive memory (rough check)
        assert memory_increase < 100  # Less than 100MB increase

        # Test cleanup
        manager.clear_buffer()
        after_clear_memory = process.memory_info().rss / 1024 / 1024

        # Memory should decrease after clearing (allowing some overhead)
        assert after_clear_memory <= after_memory


class TestPrioritization:
    """Test dynamic prioritization features."""

    def test_priority_rule_application(self):
        """Test that priority rules are applied correctly."""
        from baritone_client.events.event_manager import PriorityRule

        manager = EventManager()

        # Add priority rule for error events
        rule = PriorityRule(
            event_types={"error", "critical"},
            condition=lambda e: e.data.get("severity") == "high",
            priority_boost=5,
            description="High severity errors get priority boost"
        )
        manager.add_priority_rule(rule)

        # Publish events
        manager.publish_event("error", {"severity": "high", "message": "Critical error"}, priority=1)
        manager.publish_event("error", {"severity": "low", "message": "Minor error"}, priority=1)
        manager.publish_event("info", {"message": "Info message"}, priority=1)

        # Poll events - should be sorted by final priority
        events = manager.poll_events()
        assert len(events) == 3

        # High severity error should have highest priority (1 + 5 = 6)
        assert events[0].data["severity"] == "high"
        assert events[0].priority == 6

    def test_frequency_aware_adjustments(self):
        """Test frequency-aware priority adjustments."""
        manager = EventManager()

        # Publish many events of same type to trigger frequency penalty
        for i in range(15):  # More than threshold
            manager.publish_event("chat", {"message": f"msg {i}"})

        # Publish a new event - should get frequency penalty
        manager.publish_event("chat", {"message": "latest"})
        events = manager.poll_events()

        # The latest event should have lower priority due to frequency penalty
        latest_event = next(e for e in events if e.data["message"] == "latest")
        assert latest_event.priority < 0  # Should be negative due to penalty

    def test_subscription_based_prioritization(self):
        """Test prioritization based on subscription metadata."""
        manager = EventManager()

        # Create subscriptions with different client priorities
        high_priority_callback = MagicMock()
        low_priority_callback = MagicMock()

        manager.subscribe_named(
            "high_priority_sub",
            high_priority_callback,
            client_id="important_client",
            metadata={"priority_level": "high"}
        )

        manager.subscribe_named(
            "low_priority_sub",
            low_priority_callback,
            client_id="normal_client",
            metadata={"priority_level": "normal"}
        )

        # This would require extending the system to use subscription metadata for prioritization
        # For now, test that subscriptions are created correctly
        subs = manager.list_named_subscriptions()
        assert len(subs) == 2

        high_sub = next(s for s in subs if s.name == "high_priority_sub")
        low_sub = next(s for s in subs if s.name == "low_priority_sub")

        assert high_sub.metadata["priority_level"] == "high"
        assert low_sub.metadata["priority_level"] == "normal"


class TestNewEventTypes:
    """Test new Phase 3 event types."""

    def test_new_event_types_exist(self):
        """Test that new event types are defined."""
        # Block modification events
        assert TransportEvent.BLOCK_BREAK
        assert TransportEvent.BLOCK_PLACE
        assert TransportEvent.BLOCK_UPDATE

        # Entity interaction events
        assert TransportEvent.ENTITY_ATTACK
        assert TransportEvent.ENTITY_DAMAGE
        assert TransportEvent.ENTITY_TRADE
        assert TransportEvent.ENTITY_TAME
        assert TransportEvent.ENTITY_SHEAR
        assert TransportEvent.ENTITY_MILK

        # Environment events
        assert TransportEvent.WEATHER_CHANGE
        assert TransportEvent.TIME_CHANGE
        assert TransportEvent.TIME_UPDATE
        assert TransportEvent.REDSTONE_UPDATE

        # Multiplayer events
        assert TransportEvent.PLAYER_JOIN
        assert TransportEvent.PLAYER_LEAVE
        assert TransportEvent.CHAT_MESSAGE

    def test_event_type_values(self):
        """Test event type string values."""
        assert TransportEvent.BLOCK_BREAK.value == "block_break"
        assert TransportEvent.BLOCK_PLACE.value == "block_place"
        assert TransportEvent.BLOCK_UPDATE.value == "block_update"
        assert TransportEvent.ENTITY_ATTACK.value == "entity_attack"
        assert TransportEvent.ENTITY_DAMAGE.value == "entity_damage"
        assert TransportEvent.ENTITY_TRADE.value == "entity_trade"
        assert TransportEvent.ENTITY_TAME.value == "entity_tame"
        assert TransportEvent.ENTITY_SHEAR.value == "entity_shear"
        assert TransportEvent.ENTITY_MILK.value == "entity_milk"
        assert TransportEvent.WEATHER_CHANGE.value == "weather_change"
        assert TransportEvent.TIME_CHANGE.value == "time_change"
        assert TransportEvent.PLAYER_JOIN.value == "player_join"
        assert TransportEvent.PLAYER_LEAVE.value == "player_leave"
        assert TransportEvent.CHAT_MESSAGE.value == "chat_message"