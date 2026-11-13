# Windows AI server

These are the supplied Windows AI API files. Copy `server.py`, `tools.py`, `academic_voice.py`, `requirements.txt`, and `.env.example` into the original `C:\MondayServer` project directory before following the installation steps in the Hackster article.

Install dependencies with `pip install -r requirements.txt`, configure `.env`, ensure Ollama is running with the configured model, then start the API from `C:\MondayServer` with:

```powershell
.\.venv\Scripts\python.exe -m uvicorn server:app --host 0.0.0.0 --port 8000
```

The latest supplied server code defaults to `qwen2.5:3b`. The actual Whisper API implementation is not included; see `Windows_Server/Whisper_STT/`.
