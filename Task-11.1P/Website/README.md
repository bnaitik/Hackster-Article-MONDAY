# Website API and frontend

This folder contains the supplied Monday Website API (`monday_web.py`) and its matching static frontend (`static/`).

The original Windows deployment keeps `monday_web.py`, `static/`, and the shared `.env` in `C:\MondayServer` alongside the AI API files. To reproduce that layout, copy `monday_web.py` and the complete `static` folder into `C:\MondayServer`.

Start the website API from the Windows project directory with:

```powershell
.\.venv\Scripts\python.exe -m uvicorn monday_web:app --host 0.0.0.0 --port 8002
```

`Start_Monday_Web_API.ps1` contains the same startup command for the original `C:\MondayServer` layout.
The website and AI server must use the same `MONDAY_DB_PATH` and `MONDAY_API_TOKEN` values so they share task, alarm, and session data.
