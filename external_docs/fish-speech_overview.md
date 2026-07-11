One of the LLM sources for this project is a fish-speech TTS model which is run through a python web server (gradio).
It is handled by a user systemd service. This service doesn't run on startup but can be manually started/stopped (see below).
It is GPU-intensive and shouldn't be run at the same time as llamacpp or Image Generation
The typical commands to run it are:

```bash
# Starting
systemctl --user start fishspeech

# Stopping
systemctl --user stop fishspeech
```

This creates a webserver which by default runs on 
http://127.0.0.1:7860/

Sample client code (which runs within the project venv) is found in "fish-sentences.py"
This client code brings in the gradio-client dependency.
python ./fish-sentences.py --input_dir books/wodehouse/right-ho-jeeves/json/source/ --output_dir books/wodehouse/right-ho-jeeves/audio
