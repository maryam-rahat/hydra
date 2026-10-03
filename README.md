# Hydra

A local hydration buddy built for a friend. An open-weight model running on Ollama writes each reminder in her voice, based on her habits and how much she has had today. No API key, no cloud, no cost per message, and her data stays on her laptop.

## Setup (Windows)

1. Install Ollama from ollama.com, then:
```
   ollama pull llama3.2:3b
   pip install requests
```
2. Run:
```
   python hydra.py
```
   Use `pythonw hydra.py` for no console window. Errors go to `hydra.log`.
3. On first run, `config.json` is created. Edit `friend_name`, `tone` and `about_friend` to personalize it. See `config.example.json`.

## How it works

- A startup window asks for today's goal and how much you've had.
- A window with +250 / -250 buttons logs water.
- A background thread checks every 30 seconds and sends a Windows toast every `interval_min` minutes, during `active_hours`, until the goal is reached.
- If Ollama is down, it falls back to a plain "X ml to go" message.

## Why open source

- Runs offline.
- Her habit data never leaves her machine.
- Swap the model by changing `model` in `config.json`.
- Change the personality by editing `tone` and `about_friend`.
