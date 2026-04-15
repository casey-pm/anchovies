#!/bin/bash
#
# Start the Anchovies Hybrid Chat System
#
# Layout: Tabs (windows) instead of split panes
# - Tab 0 (chat): Chat Hub - Marcus as coordinator
# - Tab 1+ : Work sessions for personas (spawned on demand)
#
# Usage: ./start_anchovies.sh [--fresh]
#   --fresh: Kill existing session and start new

set -e

SESSION="anchovies"
ANCHOVIES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PARADISE_BRAIN="$(dirname "$ANCHOVIES_DIR")"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}╔════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     🐟 ANCHOVIES - Hybrid Chat System      ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════╝${NC}"
echo ""

# Check for --fresh flag
if [[ "$1" == "--fresh" ]]; then
    echo -e "${YELLOW}Killing existing session...${NC}"
    tmux kill-session -t $SESSION 2>/dev/null || true
fi

# Check if session already exists
if tmux has-session -t $SESSION 2>/dev/null; then
    echo -e "${GREEN}Session '$SESSION' already exists.${NC}"
    echo ""
    echo -e "${BLUE}Keyboard shortcuts:${NC}"
    echo "  Ctrl+b 0    → Jump to chat tab"
    echo "  Ctrl+b n/p  → Next/previous tab"
    echo "  Ctrl+b d    → Detach (session keeps running)"
    echo ""
    echo "Attaching..."
    exec tmux attach-session -t $SESSION
fi

echo -e "${GREEN}Creating new session...${NC}"

# Create new detached session with 'chat' window
tmux new-session -d -s $SESSION -n "chat" -c "$ANCHOVIES_DIR" -x 200 -y 50

# Enable mouse support
tmux set-option -t $SESSION mouse on 2>/dev/null || true

# Persistent status bar at bottom with quick reference keybindings
tmux set-option -t $SESSION status-left " 🐟 #S | " 2>/dev/null || true
tmux set-option -t $SESSION status-left-length 40 2>/dev/null || true
tmux set-option -t $SESSION status-right " [C-b 0]chat [C-b n/p]next/prev [C-b &]close [C-b d]detach [C-b ?]help " 2>/dev/null || true
tmux set-option -t $SESSION status-right-length 80 2>/dev/null || true
tmux set-option -t $SESSION status-bg colour24 2>/dev/null || true
tmux set-option -t $SESSION status-fg colour255 2>/dev/null || true

# Display the help banner in the chat pane so it appears in scrollback
tmux send-keys -t $SESSION:chat "clear && $ANCHOVIES_DIR/scripts/help_banner.sh" Enter

echo ""
echo -e "${GREEN}Session created!${NC}"
echo ""
echo "Attaching... (help banner will display in the chat pane — scroll up anytime to re-read)"
echo "Run './scripts/help_banner.sh' inside any pane to show it again."

exec tmux attach-session -t $SESSION
