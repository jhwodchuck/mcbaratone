"""
Shared utilities for functional test suites.
"""

from typing import Dict, Any

def get_test_state(suite_state: Dict[str, Any], test_id: str) -> Dict[str, Any]:
    """
    Get or create a state dictionary for a specific test ID.
    
    Replaces the common _state(tid) helper found in many suites.
    """
    return suite_state.setdefault(test_id, {})
