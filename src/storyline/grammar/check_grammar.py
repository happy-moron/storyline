import json

def parse_input(json_data):
    """
    Parses the input JSON data into tokens and a constituency tree.
    """
    constituency_tree = parse_constituency_bracket(json_data['c'])
    return {
        'tokens': json_data['t'],
        'constituency_tree': constituency_tree
    }

def parse_constituency_bracket(s):
    """
    Parses a constituency graph in bracket notation into a tree structure.
    """
    def parse_node(s, index):
        index[0] += 1  # Skip the opening '['
        label = []
        while index[0] < len(s) and s[index[0]] not in (' ', ']'):
            label.append(s[index[0]])
            index[0] += 1
        label = ''.join(label)
        node = {'label': label, 'children': []}
        while index[0] < len(s) and s[index[0]] == ' ':
            index[0] += 1
        while index[0] < len(s) and s[index[0]] != ']':
            if s[index[0]] == '[':
                child = parse_node(s, index)
                node['children'].append(child)
            else:
                word = []
                while index[0] < len(s) and s[index[0]] not in (' ', ']'):
                    word.append(s[index[0]])
                    index[0] += 1
                node['children'].append(''.join(word))
            while index[0] < len(s) and s[index[0]] == ' ':
                index[0] += 1
        if index[0] < len(s) and s[index[0]] == ']':
            index[0] += 1
        return node

    index = [0]
    return parse_node(s, index)


def is_hsk1_1(sentence_data):
    """
    Check if a sentence matches the HSK1-1 grammar point: "(正) 在 + Verb"
    The pattern should have "在" (zài) followed by a verb to indicate action in progress.
    
    Args:
        sentence_data (dict): Input sentence data in the specified JSON format
        
    Returns:
        bool: True if the sentence matches the grammar pattern, False otherwise
    """
    tokens = sentence_data['t']
    constituency_tree = parse_constituency_bracket(sentence_data['c'])
    
    # First check: Look for "在" followed by a verb in the token sequence
    found_zai = False
    verb_after_zai = False
    
    for i in range(len(tokens) - 1):
        text, pinyin, pos, *_ = tokens[i]
        next_text, next_pinyin, next_pos, *_ = tokens[i+1]
        
        if text == '在' and 'v' in next_pos:
            return True
        
        # Also check for "正在" pattern
        if text == '正' and next_text == '在' and i+2 < len(tokens) and 'v' in tokens[i+2][2]:
            return True
    
    # Second check: Look in the constituency tree for a VP containing "在" followed by a verb
    def check_node(node):
        if isinstance(node, dict):
            if node['label'] == 'VP':
                children_texts = [child if isinstance(child, str) else ' '.join(check_node(child)) 
                                 for child in node['children']]
                children_text = ' '.join(children_texts)
                if '在' in children_text:
                    # Check if there's a verb after 在
                    zai_index = children_text.index('在')
                    remaining = children_text[zai_index+1:]
                    # Look for any verb in the remaining part
                    for token in tokens:
                        if token[0] in remaining and 'v' in token[2]:
                            return True
            for child in node['children']:
                if check_node(child):
                    return True
        return False
    
    if check_node(constituency_tree):
        return True
    
    return False


def is_hsk2_41(parsed_data):
    """
    Checks if the sentence matches the 'hsk2-41' grammar point: two consecutive verb phrases.
    """
    def has_consecutive_vps(node):
        if isinstance(node, dict):
            children = node.get('children', [])
            for i in range(len(children) - 1):
                current = children[i]
                next_child = children[i + 1]
                if isinstance(current, dict) and current.get('label') == 'VP' and \
                   isinstance(next_child, dict) and next_child.get('label') == 'VP':
                    return True
            for child in children:
                if isinstance(child, dict) and has_consecutive_vps(child):
                    return True
        return False

    constituency_tree = parsed_data['constituency_tree']
    return has_consecutive_vps(constituency_tree)

def is_hsk1_2(sentence):
    """
    Checks if the sentence matches the HSK1-2 grammar point: "没 + 有" pattern.
    Returns True if the sentence contains "没" immediately followed by "有".
    """
    tokens = sentence['t']
    for i in range(len(tokens) - 1):
        current_token = tokens[i][0]  # Chinese character
        next_token = tokens[i + 1][0]  # Next Chinese character
        if current_token == "没" and next_token == "有":
            return True
    return False


def is_hsk2_2(sentence):
    """
    Check if the sentence matches the grammar pattern '就是 + Verb'.
    
    Args:
        sentence (dict): The parsed sentence containing tokens and constituency graph.
    
    Returns:
        bool: True if the sentence matches the pattern, False otherwise.
    """
    tokens = sentence.get('t', [])
    for i in range(len(tokens)):
        current_token = tokens[i]
        if current_token[0] == '就是':
            # Check subsequent tokens for a verb, allowing intervening adverbs and skipping punctuation/modal particles
            j = i + 1
            while j < len(tokens):
                next_token = tokens[j]
                pos = next_token[2]
                if pos in ('w', 'y'):  # Skip punctuation and modal particles
                    j += 1
                elif pos == 'adv':     # Allow adverbs between 就是 and the verb
                    j += 1
                elif pos == 'v':       # Found the verb
                    return True
                else:                  # Other POS breaks the search
                    break
    return False

def is_hsk1_3(sentence):
    """
    Checks if the sentence follows the "Standard negation with 'bu'" pattern (不 + Verb/Adj).
    
    Args:
        sentence (dict): Input sentence in the specified JSON format with tokens and constituency.
        
    Returns:
        bool: True if the sentence matches the grammar pattern, False otherwise.
    """
    # First check tokens for "不" followed by verb or adjective
    tokens = sentence['t']
    for i in range(len(tokens) - 1):
        if tokens[i][0] == '不' and tokens[i][1] == 'bù':
            next_pos = tokens[i+1][2]
            if 'v' in next_pos or 'a' in next_pos:
                return True
    
    # Then check constituency tree for pattern
    tree = parse_constituency_bracket(sentence['c'])
    
    def find_negation(node):
        if isinstance(node, dict):
            if node['label'] == 'VP':
                children = node['children']
                for i in range(len(children) - 1):
                    if isinstance(children[i], dict) and children[i]['label'] == 'ADVP':
                        advp = children[i]
                        if any(isinstance(child, str) and child == '不' for child in advp.get('children', [])):
                            next_child = children[i+1]
                            if isinstance(next_child, dict) and next_child['label'] == 'VP':
                                return True
                    elif isinstance(children[i], str) and children[i] == '不':
                        return True
            for child in node['children']:
                if find_negation(child):
                    return True
        return False
    
    return find_negation(tree)