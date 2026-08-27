# Part 3 - generate the mp3 audio for the podcast episode

Update the create podcast pipeline with a final stage to create the mp3 audio of the podcast.

## Additional Audio to generate

It will need to generate audio for:

- the hosts saying the dialogue sentences (teacher saying Chinese, student saying English translation)
- The hosts saying their intro lines
- The hosts saying their breakdown lines
- The hosts saying their outro lines.

## Script format for the mp3 audio

The MP3 audio should have expanded content from the base script, as follows. 
It should re-use the generated sentence audio for the e-reader (books/podcasts/<episode>/audio) if it exists. This can save it having to redo the voice design and audio generation for the dialogue portion.

The generated lines then need to be switched together into the full podcast sequence:

* Intro
* Dialogue
  * the full dialogue sequence, repeated twice for a total of three times in the character voices
* Dialogue - Line By Line with English Translation
  * The teacher host reads a line in chinese, and the student host reads the English translation. This is done 3x for each line. 
* Breakdown of Dialogue
* Outro 

## Audio Gen guidelines

* The pipeline should generate all the audio using the "Voice clone" feature 
  * hosts should use the pre-existing reference audio
      * voices/student.txt & voices/student.wav
      * voices/teacher.txt & voices/teacher.wav
  * Dialogue characters should use the generated voice samples (and reference text from the "dialogue" property in their voice files)
* The mp3 should be generated in ~/temp/audio/podcasts/<episode-name>.mp3 
* The mp3 metadata should have an author of 'podcasts' and a name of the episode-name

