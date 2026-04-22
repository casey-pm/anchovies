"""
Handlers package — message routing, control commands, session spawning, conversation memory.

Re-exports public API so existing imports from anchovies.handlers still work.
"""

# Routing (main entry points)
from .routing import (
    handle_team_message,
    handle_chat_hub_message,
    get_chat_hub,
    detect_summons_in_response,
    process_member_response,
    should_route_to_chat_hub,
    chain_depth,
    MAX_CHAIN_DEPTH,
)

# Memory
from .memory import (
    conversation_store,
    get_conversation_history,
    add_to_conversation,
    MAX_THREADS,
    MAX_MESSAGES_PER_THREAD,
    THREAD_TTL_SECONDS,
    _cleanup_old_threads,
    _thread_last_accessed,
)

# Control commands
from .control_commands import (
    handle_control_command as _handle_control_command,
    parse_project_command,
    handle_project_command,
    is_paused,
)

# _paused state lives in control_commands module — use is_paused() to check

# Spawner
from .spawner import (
    spawn_session_for_task,
    auto_spawn_from_director as _auto_spawn_from_director,
    save_project_spec as _save_project_spec,
)

# Backwards compat alias
detect_mentions_in_response = detect_summons_in_response
