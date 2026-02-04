"""
Skill Mapper - Maps tasks to relevant skills and commands.

Determines which skills, commands, and tools are relevant for a given task type.
"""

import re

# Task type definitions with associated skills
TASK_SKILLS = {
    "bug_fix": [
        "Read the relevant file(s) to understand the context",
        "Use /commit to commit your changes when the fix is ready",
        "Run tests to verify the fix: pytest <test_file>",
        "Check for similar issues in related code",
    ],
    "new_feature": [
        "Create new file(s) as needed",
        "Use /commit to commit your changes",
        "Write tests for new functionality in tests/ directory",
        "Update documentation if adding public APIs",
    ],
    "code_edit": [
        "Read the file first to understand current implementation",
        "Make targeted edits (prefer Edit tool over full rewrites)",
        "Use /commit when changes are complete",
        "Test your changes",
    ],
    "testing": [
        "Run tests with: pytest <path>",
        "Run specific test: pytest <path>::<test_name>",
        "Run with verbose output: pytest -v <path>",
        "Check test coverage if needed",
    ],
    "refactor": [
        "Understand the current code structure first",
        "Make incremental changes and test frequently",
        "Use /commit after each logical change",
        "Ensure no functionality is broken",
    ],
    "documentation": [
        "Read existing documentation for style consistency",
        "Update relevant .md files",
        "Use /commit for documentation changes",
        "Keep documentation concise and accurate",
    ],
    "css_styling": [
        "Check existing CSS patterns in style.css",
        "Follow WeasyPrint compatibility (no gap, box-shadow, transition)",
        "Use margin-based spacing as fallback for gap",
        "Test with PDF generation if applicable",
    ],
    "data_analysis": [
        "Use pandas for data manipulation",
        "Create visualizations with matplotlib/seaborn",
        "Document findings clearly",
        "Export results to appropriate format",
    ],
    "sql_query": [
        "Always use bi_playground dataset for ad-hoc objects (not reporting)",
        "Verify column names with INFORMATION_SCHEMA before querying",
        "Handle NULL values explicitly in comparisons",
        "Test queries with LIMIT before running on full data",
    ],
    "api_integration": [
        "Check API documentation first",
        "Handle errors and edge cases",
        "Implement rate limiting if needed",
        "Log API responses for debugging",
    ],
    "git_operations": [
        "/commit - Create a commit with your changes",
        "Review changes with git diff before committing",
        "Write clear commit messages describing the change",
    ],
    "general": [
        "Read relevant files to understand context",
        "Make changes incrementally",
        "Test your changes",
        "Use /commit when ready",
    ],
}

# Patterns to detect task types
TASK_TYPE_PATTERNS = {
    "bug_fix": [
        r"\b(fix|debug|resolve|repair)\b.*\b(bug|error|issue|problem|crash)\b",
        r"\b(bug|error|issue)\b.*\b(fix|resolve)\b",
        r"not working",
        r"broken",
        r"failing",
    ],
    "new_feature": [
        r"\b(create|add|implement|build|write)\b.*\b(new|feature|function|class|module)\b",
        r"\bnew\b.*\b(file|function|class)\b",
    ],
    "code_edit": [
        r"\b(edit|modify|change|update|alter)\b.*\b(file|code|function|line)\b",
        r"\b(change|update)\b.*\bto\b",
    ],
    "testing": [
        r"\b(write|create|add|run)\b.*\btests?\b",
        r"\btest(s|ing)?\b",
        r"\bpytest\b",
    ],
    "refactor": [
        r"\brefactor\b",
        r"\brestructure\b",
        r"\breorganize\b",
        r"\bclean\s*up\b",
    ],
    "documentation": [
        r"\b(document|docs?|readme)\b",
        r"\b(write|update|add)\b.*\bdocumentation\b",
        r"\.md\b",
    ],
    "css_styling": [
        r"\bcss\b",
        r"\bstyl(e|ing)\b",
        r"\blayout\b",
        r"\bdesign\b",
        r"\.css\b",
    ],
    "data_analysis": [
        r"\banalyz(e|is)\b",
        r"\bdata\b.*\b(analysis|explore|investigate)\b",
        r"\bmetrics?\b",
        r"\bstatistics?\b",
    ],
    "sql_query": [
        r"\bsql\b",
        r"\bquery\b",
        r"\bbigquery\b",
        r"\b(select|insert|update|delete)\b.*\bfrom\b",
        r"\.sql\b",
    ],
    "api_integration": [
        r"\bapi\b",
        r"\bintegrat(e|ion)\b",
        r"\bendpoint\b",
        r"\brequest\b",
    ],
    "git_operations": [
        r"\bcommit\b",
        r"\bgit\b",
        r"\bpush\b",
        r"\bpull\s*request\b",
        r"\bpr\b",
    ],
}

# Compile patterns
COMPILED_PATTERNS = {
    task_type: [re.compile(p, re.IGNORECASE) for p in patterns]
    for task_type, patterns in TASK_TYPE_PATTERNS.items()
}


def detect_task_type(task_description: str) -> str:
    """
    Detect the type of task from the description.

    Args:
        task_description: The task description text

    Returns:
        Task type string (e.g., "bug_fix", "new_feature", etc.)
    """
    scores = {}

    for task_type, patterns in COMPILED_PATTERNS.items():
        score = sum(1 for p in patterns if p.search(task_description))
        if score > 0:
            scores[task_type] = score

    if not scores:
        return "general"

    # Return the task type with highest score
    return max(scores, key=scores.get)


def get_skills_for_task(task_type: str, task_description: str = "") -> list[str]:
    """
    Get relevant skills for a task type.

    Args:
        task_type: The detected task type
        task_description: Optional task description for additional context

    Returns:
        List of relevant skills/commands
    """
    skills = TASK_SKILLS.get(task_type, TASK_SKILLS["general"]).copy()

    # Add persona-specific skills based on keywords
    if "dbt" in task_description.lower():
        skills.extend([
            "Run dbt models: dbt run --select <model>",
            "Test dbt models: dbt test --select <model>",
        ])

    if "report" in task_description.lower():
        skills.extend([
            "Generate test report to verify changes",
            "Check PDF output with WeasyPrint",
        ])

    if "slack" in task_description.lower():
        skills.extend([
            "Test Slack integration locally",
            "Check Slack API responses",
        ])

    return skills


def get_all_task_types() -> list[str]:
    """Get a list of all known task types."""
    return list(TASK_SKILLS.keys())


# Test function
if __name__ == "__main__":
    test_descriptions = [
        "Fix the NoneType error in data_processor.py",
        "Create a new function to handle API responses",
        "Write tests for the user authentication module",
        "Refactor the report generator for better performance",
        "Update the CSS styling for the dashboard",
        "Analyze the traffic data for anomalies",
        "Write a SQL query to get user metrics",
        "Commit the changes to the repository",
        "Help me understand this code",
    ]

    print("Testing task type detection:")
    print("=" * 60)

    for desc in test_descriptions:
        task_type = detect_task_type(desc)
        skills = get_skills_for_task(task_type, desc)

        print(f"\nTask: {desc}")
        print(f"  Type: {task_type}")
        print(f"  Skills ({len(skills)}):")
        for skill in skills[:3]:
            print(f"    - {skill}")
        if len(skills) > 3:
            print(f"    ... and {len(skills) - 3} more")
