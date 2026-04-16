#!/bin/bash
#
# Display the Anchovies help banner.
# Run on tmux session start, and available anytime via `./scripts/help_banner.sh`.

# Colors
BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

clear

echo -e "${BLUE}╔══════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║${BOLD}                   🐟 ANCHOVIES - Quick Reference                  ${NC}${BLUE}║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════════════╝${NC}"
echo ""

echo -e "${BOLD}${CYAN}TMUX SHORTCUTS${NC}  ${DIM}(all start with Ctrl+b, then press the key)${NC}"
echo -e "  ${GREEN}Ctrl+b 0${NC}          Jump to chat tab (Marcus)"
echo -e "  ${GREEN}Ctrl+b 1${NC}-${GREEN}9${NC}        Jump to persona tab by number"
echo -e "  ${GREEN}Ctrl+b n${NC}          Next tab"
echo -e "  ${GREEN}Ctrl+b p${NC}          Previous tab"
echo -e "  ${GREEN}Ctrl+b w${NC}          List all tabs (interactive picker)"
echo -e "  ${GREEN}Ctrl+b &${NC}          Close current tab ${DIM}(asks y/n)${NC}"
echo -e "  ${GREEN}Ctrl+b d${NC}          Detach ${DIM}(session keeps running — reattach with 'tmux attach -t anchovies')${NC}"
echo -e "  ${GREEN}Ctrl+b ?${NC}          Show all tmux keybindings"
echo ""

echo -e "${BOLD}${CYAN}SPAWNING PERSONAS${NC}"
echo -e "  ${YELLOW}./scripts/spawn_persona.sh <name>${NC}                  Spawn a persona with default prompt"
echo -e "  ${YELLOW}./scripts/spawn_persona.sh <name> <prompt_file>${NC}    Spawn with a custom task prompt"
echo ""
echo -e "  ${DIM}Available personas (16):${NC}"
echo -e "  ${DIM}  marcus  sofia   raj     leo     natalie  mike${NC}"
echo -e "  ${DIM}  anna    tom     james   priya   elena    julia${NC}"
echo -e "  ${DIM}  olivia  nina    victor  kai${NC}"
echo ""

echo -e "${BOLD}${CYAN}POSTING TO SLACK${NC}"
echo -e "  ${YELLOW}./scripts/slack \"message\"${NC}                          Post as default member"
echo -e "  ${YELLOW}./scripts/slack \"message\" --member <name>${NC}          Post as a specific persona"
echo -e "  ${YELLOW}./scripts/slack \"message\" --thread <ts>${NC}            Reply to a Slack thread"
echo ""

echo -e "${BOLD}${CYAN}PROJECTS${NC}  ${DIM}(manage via Slack or edit projects.yaml)${NC}"
echo -e "  ${YELLOW}@bot projects${NC}                                      List all registered projects"
echo -e "  ${YELLOW}@bot add project calc --context ~/calc${NC}             Register a new project"
echo -e "  ${YELLOW}@bot remove project calc${NC}                           Unregister a project"
echo -e "  ${YELLOW}@bot set default project calc${NC}                      Set the default project"
echo -e "  ${YELLOW}@bot project info calc${NC}                             Show project details"
echo -e "  ${DIM}Use [project] tags in messages: @bot [calc] fix the bug${NC}"
echo ""

echo -e "${BOLD}${CYAN}MANAGING SESSIONS${NC}"
echo -e "  ${YELLOW}tmux list-windows${NC}                                  List all active tabs"
echo -e "  ${YELLOW}tmux kill-window -t anchovies:<name>${NC}               Close a specific persona tab"
echo -e "  ${YELLOW}tmux kill-session -t anchovies${NC}                     Kill everything ${DIM}(no undo)${NC}"
echo ""

echo -e "${BOLD}${CYAN}STARTING THE SLACK BOT${NC}"
echo -e "  ${YELLOW}cd ~/paradise_brain && python -m anchovies.app${NC}     Start the Slack listener"
echo -e "  ${DIM}Must run from paradise_brain/, NOT from inside anchovies/${NC}"
echo ""

echo -e "${BOLD}${CYAN}RUNNING TESTS${NC}"
echo -e "  ${YELLOW}cd ~/paradise_brain/anchovies && python -m pytest tests/${NC}"
echo ""

echo -e "${BOLD}${CYAN}SHOW THIS HELP AGAIN${NC}"
echo -e "  ${YELLOW}./scripts/help_banner.sh${NC}"
echo ""

echo -e "${MAGENTA}───────────────────────────────────────────────────────────────────${NC}"
echo ""
