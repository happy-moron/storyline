# Goal

To allow a breakdown text to be viewed for each reading line in a chapter (if it exists/is generated.)
This is logically similar but completely separate to the 'breakdown' section of the podcast which is rendered into the audio mp3 but is not part of the e-reader.

# Pieces needed to be implemented

This means:

* creating a dedicated prompt to generate the breakdown section with and improved detail for each line (manual action, done first) - @prompts/breakdown.md
* designing and testing a validator and parser which processes the results and guarantees there is breakdown content for every line, that none are skipped/missed 
  * probably needs normalized sentence matching to check entries
* Updating the create_book and podcast pipelines to generate breakdowns for each reading line
  * implementing retries to to process missed/gonked lines
* Saving the breakdowns with the rest of the generated book content
* updating the existing index.html viewer to have a toggle (similar to the 'English' language toggle) which shows the breakdown for the current/active sentence when toggled on.
  * this should support episodes which don't have breakdown content
* Updating package_reader.sh (if needed) to make sure the breakdown content is included in the final package
* Creating a backfill script which will use the service manager and generate breakdowns for existing chapters/podcasts which don't have them already

# Implementation order

## Step 0 (manual, prexisting)

Write the prompt for generating the breakdown content

## Step 1 - Parser/validator

- Review the existing prompt, understand the output format, and create the parser/validator with tests

## Step 2 - integrate into both create_book and podcast pipelines

Running the prompt and validator should happen at a natural stage in the pipeline when the reading lines exist and when the LLM is already up and started

A "fix" prompt similar to existing (e.g prompts/fix_tokenization.txt) which can generate/retry missing lines should be written to attempt to fix breakdowns that fail validation.

This should save the breakdown text content in a natural place within the generated book content.

## Step 3 - update the index.html viewer with a toggle to view the breakdown

There should be one toggle for displaying the breakdown content
When the toggel is on, the breakdown text content should only display for the active/selected sentence and should dynamically switch when different sentences are selected.

## Step 4 - verify the package reader script contains the content

- it probably should; just need to doublecheck this

## Step 5 - create a backfill script which creates content for existing books (books and podcasts)

This should use servicemanager for starting/stopping the LLM and should create the breakdown content for existing episodes, if it doesn't exist. It's conceptually similar to scripts/backfill_backchain.py and scripts/backfill_flashcard_inverted.py