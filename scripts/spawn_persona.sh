#!/bin/bash
#
# Spawn a new persona work tab in the Anchovies tmux session
#
# Usage: ./spawn_persona.sh <persona_name> [prompt_file]
#
# Examples:
#   ./spawn_persona.sh sofia
#   ./spawn_persona.sh sofia /path/to/task_prompt.txt

set -e

SESSION="anchovies"
ANCHOVIES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PARADISE_BRAIN="$(dirname "$ANCHOVIES_DIR")"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Check arguments
if [[ -z "$1" ]]; then
    echo -e "${RED}Error: Persona name required${NC}"
    echo "Usage: $0 <persona_name> [prompt_file]"
    echo ""
    echo "Available personas:"
    echo "  marcus, sofia, elena, james, victor, anna, raj, leo,"
    echo "  natalie, tom, priya, mike, nina, julia, olivia, kai"
    exit 1
fi

PERSONA="$1"
PROMPT_FILE="$2"

# Check if session exists
if ! tmux has-session -t $SESSION 2>/dev/null; then
    echo -e "${RED}Error: Session '$SESSION' does not exist${NC}"
    echo "Run ./start_anchovies.sh first"
    exit 1
fi

# Check if persona tab already exists
if tmux list-windows -t $SESSION -F "#{window_name}" | grep -q "^${PERSONA}$"; then
    if [[ -n "$PROMPT_FILE" && -f "$PROMPT_FILE" ]]; then
        echo -e "${YELLOW}Tab for '$PERSONA' already exists. Sending prompt to existing session...${NC}"

        # Load prompt and paste into existing tab
        BUFFER_NAME="prompt_${PERSONA}_$$"
        tmux load-buffer -b "$BUFFER_NAME" "$PROMPT_FILE"
        tmux paste-buffer -b "$BUFFER_NAME" -t $SESSION:$PERSONA
        sleep 1
        tmux send-keys -t $SESSION:$PERSONA Enter
        tmux delete-buffer -b "$BUFFER_NAME" 2>/dev/null || true

        echo -e "${GREEN}Prompt sent to existing '$PERSONA' tab.${NC}"
        tmux select-window -t $SESSION:$PERSONA
        exit 0
    else
        echo -e "${YELLOW}Tab for '$PERSONA' already exists. Switching to it...${NC}"
        tmux select-window -t $SESSION:$PERSONA
        exit 0
    fi
fi

# Build the system prompt
if [[ -n "$PROMPT_FILE" && -f "$PROMPT_FILE" ]]; then
    SYSTEM_PROMPT="$(cat "$PROMPT_FILE")"
    echo -e "${GREEN}Using custom prompt from: $PROMPT_FILE${NC}"
else
    # Load profile and build basic prompt
    PROFILE_FILE="$ANCHOVIES_DIR/profiles/profile_${PERSONA}.yaml"

    if [[ -f "$PROFILE_FILE" ]]; then
        # Extract basic info from profile (simple grep, not full YAML parsing)
        NAME=$(grep "^name:" "$PROFILE_FILE" | sed 's/name: *"\?\([^"]*\)"\?/\1/')
        NICKNAME=$(grep "^nickname:" "$PROFILE_FILE" | sed 's/nickname: *"\?\([^"]*\)"\?/\1/')
        ROLE=$(grep "^role:" "$PROFILE_FILE" | sed 's/role: *"\?\([^"]*\)"\?/\1/')

        SYSTEM_PROMPT="You are ${NAME} (\"${NICKNAME}\"), ${ROLE} on the Domain 360 project team.

This is a work session. You can edit files, run commands, and complete tasks.

When you complete your task:
1. Update your status file: $ANCHOVIES_DIR/status/status_${PERSONA}.md
2. Let the user know you're done
3. The session can then be closed"

        echo -e "${GREEN}Using default prompt for: $PERSONA${NC}"
    else
        echo -e "${YELLOW}Warning: Profile not found at $PROFILE_FILE${NC}"
        SYSTEM_PROMPT="You are ${PERSONA^}, a team member on the Domain 360 project.

This is a work session. You can edit files, run commands, and complete tasks."
    fi
fi

# Create temp file for system prompt
PROMPT_TMP="$ANCHOVIES_DIR/tmp/prompt_${PERSONA}.txt"
mkdir -p "$ANCHOVIES_DIR/tmp"
echo "$SYSTEM_PROMPT" > "$PROMPT_TMP"

echo -e "${GREEN}Spawning work tab for: $PERSONA${NC}"

# Create new window for the persona
tmux new-window -t $SESSION -n "$PERSONA" -c "$PARADISE_BRAIN"

# Start claude (without system prompt - we'll paste it as first message)
tmux send-keys -t $SESSION:$PERSONA "claude" Enter

echo "Waiting for Claude to start..."
sleep 18

# Use tmux load-buffer and paste-buffer to handle special characters properly
BUFFER_NAME="prompt_${PERSONA}_$$"
tmux load-buffer -b "$BUFFER_NAME" "$PROMPT_TMP"
tmux paste-buffer -b "$BUFFER_NAME" -t $SESSION:$PERSONA
sleep 1
tmux send-keys -t $SESSION:$PERSONA Enter
tmux delete-buffer -b "$BUFFER_NAME" 2>/dev/null || true

echo -e "${GREEN}Done! Tab '$PERSONA' created.${NC}"
echo ""
echo "Keyboard shortcuts:"
echo "  Ctrl+b 0    → Back to chat hub"
echo "  Ctrl+b n/p  → Next/previous tab"
