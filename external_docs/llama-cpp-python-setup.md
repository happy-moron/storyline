There is a llama.cpp server running behind a python wrapper (llama-cpp-python)
It is compatible with OpenAI v1 endpoints and supports multiple models locally hosted.

It runs on http://127.0.0.1:11432/v1

It is managed by systemctl:
systemctl --user start llamacpp
systemctl --user stop llamacpp

In the background it's running within a 'llamacpp' conda environment and its configuration file lives in /home/zspdude/llamacpp/llamacpp.conf