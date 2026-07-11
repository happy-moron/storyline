def parse_constituency_bracket(s):
    """
    Parses a constituency graph in bracket notation into a tree structure.

    Args:
        s (str): Input string in bracket notation.

    Returns:
        dict: A tree where each node is a dictionary with:
            - 'label': The node's label (str).
            - 'children': List of child nodes (dict) or leaf strings (str).

    Example:
        Input: "[S [NP [NR Jeeves]]"
        Output: {'label': 'S', 'children': [{'label': 'NP', 'children': [{'label': 'NR', 'children': ['Jeeves']}]}]}
    """
    def parse_node(s, index):
        index[0] += 1  # Skip the opening '['
        # Extract the label
        label = []
        while index[0] < len(s) and s[index[0]] not in (' ', ']'):
            label.append(s[index[0]])
            index[0] += 1
        label = ''.join(label)
        # Create the current node
        node = {'label': label, 'children': []}
        # Skip any spaces after the label
        while index[0] < len(s) and s[index[0]] == ' ':
            index[0] += 1
        # Process children until closing ']' is found
        while index[0] < len(s) and s[index[0]] != ']':
            if s[index[0]] == '[':
                # Recursively parse a child node
                child = parse_node(s, index)
                node['children'].append(child)
            else:
                # Parse a leaf string
                word = []
                while index[0] < len(s) and s[index[0]] not in (' ', ']'):
                    word.append(s[index[0]])
                    index[0] += 1
                node['children'].append(''.join(word))
            # Skip spaces after the child
            while index[0] < len(s) and s[index[0]] == ' ':
                index[0] += 1
        # Skip the closing ']'
        if index[0] < len(s) and s[index[0]] == ']':
            index[0] += 1
        return node

    index = [0]
    return parse_node(s, index)


def serialize_constituency(node):
    """
    Serializes a constituency tree back into bracket notation.

    Args:
        node (dict or str): The root node of the tree (dict) or a leaf (str).

    Returns:
        str: The bracket notation string.

    Example:
        Input: {'label': 'S', 'children': [{'label': 'NP', 'children': [{'label': 'NR', 'children': ['Jeeves']}]}]}
        Output: "[S [NP [NR Jeeves]]]"
    """
    if isinstance(node, dict):
        child_strings = []
        for child in node['children']:
            child_str = serialize_constituency(child)
            child_strings.append(child_str)
        children_part = ' '.join(child_strings)
        return f"[{node['label']} {children_part}]"
    else:
        return node