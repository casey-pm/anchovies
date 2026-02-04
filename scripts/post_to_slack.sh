#!/bin/bash
#
# Post a message to Slack from a work session
#
# Usage: ./post_to_slack.sh "message" [--thread THREAD_TS]
#
# Environment variables required:
#   SLACK_BOT_TOKEN - Bot token (xoxb-...)
#   SLACK_CHANNEL_ID - Channel to post to
#
# Can also be called as: /slack "message"
# (if aliased in shell)

set -e

ANCHOVIES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Load .env if it exists
if [[ -f "$ANCHOVIES_DIR/.env" ]]; then
    export $(grep -v '^#' "$ANCHOVIES_DIR/.env" | xargs)
fi

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Parse arguments
MESSAGE=""
THREAD_TS=""
MEMBER=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --thread|-t)
            THREAD_TS="$2"
            shift 2
            ;;
        --member|-m)
            MEMBER="$2"
            shift 2
            ;;
        *)
            MESSAGE="$1"
            shift
            ;;
    esac
done

# Format message with member name if provided
if [[ -n "$MEMBER" ]]; then
    # Capitalize first letter
    MEMBER_CAP="$(echo "${MEMBER:0:1}" | tr '[:lower:]' '[:upper:]')${MEMBER:1}"
    MESSAGE="*${MEMBER_CAP}:* ${MESSAGE}"
fi

# Validate
if [[ -z "$MESSAGE" ]]; then
    echo -e "${RED}Error: Message required${NC}"
    echo "Usage: $0 \"message\" [--member NAME] [--thread THREAD_TS]"
    echo ""
    echo "Options:"
    echo "  --member, -m NAME    Prefix message with team member name"
    echo "  --thread, -t TS      Reply to a thread"
    exit 1
fi

if [[ -z "$SLACK_BOT_TOKEN" ]]; then
    echo -e "${RED}Error: SLACK_BOT_TOKEN not set${NC}"
    echo "Set it in $ANCHOVIES_DIR/.env or export it"
    exit 1
fi

if [[ -z "$SLACK_CHANNEL_ID" ]]; then
    echo -e "${YELLOW}Warning: SLACK_CHANNEL_ID not set, using default${NC}"
    # You can set a default here or require it
    echo -e "${RED}Error: SLACK_CHANNEL_ID required${NC}"
    exit 1
fi

# Build JSON payload
if [[ -n "$THREAD_TS" ]]; then
    PAYLOAD=$(cat <<EOF
{
    "channel": "$SLACK_CHANNEL_ID",
    "text": "$MESSAGE",
    "thread_ts": "$THREAD_TS"
}
EOF
)
else
    PAYLOAD=$(cat <<EOF
{
    "channel": "$SLACK_CHANNEL_ID",
    "text": "$MESSAGE"
}
EOF
)
fi

# Post to Slack
RESPONSE=$(curl -s -X POST "https://slack.com/api/chat.postMessage" \
    -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD")

# Check response
if echo "$RESPONSE" | grep -q '"ok":true'; then
    echo -e "${GREEN}✓ Message posted to Slack${NC}"
else
    echo -e "${RED}✗ Failed to post message${NC}"
    echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
    exit 1
fi
