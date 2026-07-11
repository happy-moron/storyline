import json                                                                                                                              
                                                                                                                                         
def preprocess(json_string):                                                                                                             
    data = json.loads(json_string)                                                                                                       
    chinese_sentences = [item['chinese'] for item in data]                                                                               
    return "\n".join(chinese_sentences)                                                                                                  
                                                                                                                                         
if __name__ == '__main__':                                                                                                               
    # Example usage (for testing purposes)                                                                                               
    json_content = """                                                                                                                   
    [                                                                                                                                    
      {                                                                                                                                  
        "chinese": "“在埋伏中”",                                                                                                         
        "english": "\"In Ambush.\""                                                                                                      
      },                                                                                                                                 
      {                                                                                                                                  
        "chinese": "夏天的时候，好的男孩们会在学校后面的小山上建造小房子。",                                                             
        "english": "In the summer, good boys would build small houses on the hill behind the school."                                    
      },                                                                                                                                 
      {                                                                                                                                  
        "chinese": "这些小房子是用带刺的灌木做的，里面有很多树桩和树根。",                                                               
        "english": "These small houses were made of thorny bushes, and inside there were many stumps and roots."                         
      },                                                                                                                                 
      {                                                                                                                                  
        "chinese": "因为学校不允许，所以我们觉得这些小房子特别好。",                                                                     
        "english": "Because the school didn't allow it, we felt these little houses were especially good."                               
      },                                                                                                                                 
      {                                                                                                                                  
        "chinese": "这是Stalky，McTurk和Beetle连续第五年建造这样的地方了 (那时他们还没有自己的书房)。",                                  
        "english": "This was the fifth year in a row that Stalky, McTurk, and Beetle had built such a place (they didn't have their own  
study at that time)."                                                                                                                    
      },                                                                                                                                 
      {                                                                                                                                  
        "chinese": "他们像海狸一样建造了这个可以休息和思考的地方，他们还在那里抽烟。",                                                   
        "english": "They built this place to rest and think, like beavers, and they also smoked there."                                  
      }                                                                                                                                  
    ]                                                                                                                                    
    """                                                                                                                                  
    output = preprocess(json_content)                                                                                                    
    print(output)