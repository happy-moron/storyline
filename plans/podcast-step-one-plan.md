# Step One - Podcast script generation

Build up the pipeline step by step, with unit tests at each step.

Background files:

* list of topics is in src/storyline/podcast/topics.md
* list of already-created topics is in src/storyline/podcast/existing-episodes.csv
* HSK Grammar points are in dict/hsk[1-3]-grammar-index.json files
* Existing vocab prompt in prompts/podcast-vocab.md
* Existing script prompt in prompts/podcast-script.md (needs to be turned into a template for injecting theme/vocab)

## In scope

* Pipeline to suport generation of podcast scripts via LLM
    * Should use local model 'qwen38-27b' for all prompts
    * Should update src/storyline/config/llms_for_tasks.toml with new tasks for vocab generation and script creation
    * Should update the existing prompt to receive the theme and the list of vocab

## Out of scope 

* Parsing/validating the produced scripts

# Set up a cli shell similar to create_book.py

This will be the entry point for running it. It doesn't need any options to begin with.

# Set up next theme and grammar point selection 

As part of this, the pipeline:

Should read the existing-episodes.csv, topics.md, and the hsk grammar-index files.
Should implement all the logic for determining the next (not-yet-covered-in-existing) two HSK grammar points and the next topic.

* deal with normalizing theme/episode names as lowercase-atoz-with-hypens
* deal with the json parsing for HSK points

Should have full unit testing for all the different relevant cases, including non-contiguous cases where grammar points and/or topics have been covered out-of-order.

# Create vocab for the "next" theme

Set up the pipeline to initialize the LLM using the existing ServiceManager that the create_book pipeline uses.
Update llms_for_tasks.toml with a task for vocab generation

Wire the pipeline up with the existing prompt template in prompts/podcast-vocab.md

Generate vocab into a folder books_src/podcasts/vocab/<normalized-theme-name>.txt

