The podcasts are duplicating vocabulary because every podcast is just a "one shot" generation with no checking of vocab that is generated.

The answer is to have a list of vocab that can be checked before selecting vocab for an episode.

1 - consolidate all existing vocab into a manifest (done)
2 - Prior to podcast script, generate a generous list of candidate vocab (prompts/podcast-candidate-vocab.md)
3 - Dedup the candidates and remove any existing manifest vocab from the candidate list
4 - pass the candidates into the podcast script generation (prompts/podcast-script.md)
  - might 
5 - use the candidates for the flashcard rather than extracting them via flashcard-vocab-from-script.md



