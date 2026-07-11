import re


def strip_think_tags(text):
    """Remove think tags from LLM responses.

    Many thinking models include their reasoning in special tags like ␀ (U+2400)
    or similar markers. This function removes those tags and their content.

    Args:
        text: The response text from an LLM

    Returns:
        Cleaned text with think tags removed
    """
    if not text:
        return text

    # Remove common think tag patterns
    # Pattern 1: ␀think␀ content ␀/think␀ (with various delimiters)
    # Pattern 2: <think> content </think>
    # Pattern 3: ## Thinking ## content ## End Thinking ##

    # Remove ␀think␀...␀/think␀ patterns
    text = re.sub(r'␀think␀.*?␀/think␀', '', text, flags=re.DOTALL)
    text = re.sub(r'␀think␀.*?␀/think␀', '', text, flags=re.DOTALL)

    # Remove <think>...</think> patterns
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)

    # Remove ## Thinking ##...## End Thinking ## patterns
    text = re.sub(r'## Thinking ##.*?## End Thinking ##', '', text, flags=re.DOTALL)

    # Remove ## Thinking ##...## End ## patterns
    text = re.sub(r'## Thinking ##.*?## End ##', '', text, flags=re.DOTALL)

    # Remove ## Thought ##...## End Thought ## patterns
    text = re.sub(r'## Thought ##.*?## End Thought ##', '', text, flags=re.DOTALL)

    # Remove ## Thought ##...## End ## patterns
    text = re.sub(r'## Thought ##.*?## End ##', '', text, flags=re.DOTALL)

    # Remove ## reasoning ##...## end reasoning ## patterns
    text = re.sub(r'## reasoning ##.*?## end reasoning ##', '', text, flags=re.DOTALL)

    # Remove ## Reasoning ##...## End Reasoning ## patterns
    text = re.sub(r'## Reasoning ##.*?## End Reasoning ##', '', text, flags=re.DOTALL)

    # Remove any remaining standalone think tags
    text = re.sub(r'\s*␀think␀\s*', '', text)
    text = re.sub(r'\s*␀/think␀\s*', '', text)

    # Clean up any extra whitespace from removed content
    text = re.sub(r'\n\s*\n\s*\n', '\n\n', text)
    text = text.strip()

    return text


def strip_markdown_fences(text):
    """Strip surrounding markdown code fences (with or without language tag)."""
    text = text.strip()
    text = re.sub(r'^```[^\n]*\n', '', text)
    text = re.sub(r'\n```\s*$', '', text)
    return text.strip()
