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
tmux new-session -d -s $SESSION -n "chat" -c "$PARADISE_BRAIN" -x 200 -y 50

# Enable mouse support
tmux set-option -t $SESSION mouse on 2>/dev/null || true

echo ""
echo -e "${GREEN}Session created!${NC}"
echo ""
echo -e "${BLUE}Layout:${NC}"
echo "┌─────────────────────────────────────────────────────────────┐"
echo "│  [chat] [sofia] [leo] ...   ← tabs appear as you spawn     │"
echo "│                                                             │"
echo "│  Chat Hub (Marcus) - or persona work session                │"
echo "│                                                             │"
echo "└─────────────────────────────────────────────────────────────┘"
echo ""
echo -e "${BLUE}Keyboard shortcuts:${NC}"
echo "  Ctrl+b 0    → Jump to chat tab"
echo "  Ctrl+b n/p  → Next/previous tab"
echo "  Ctrl+b d    → Detach (session keeps running)"
echo ""
echo "Attaching..."

exec tmux attach-session -t $SESSION
