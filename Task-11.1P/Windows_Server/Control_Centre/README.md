# Monday Control Centre

This is the supplied optional Windows service dashboard. It uses the original fixed project path `C:\MondayServer`, the `.venv` and `.stt-venv` environments, and ports 8000–8003.

Keep this folder together so `control_centre.py` can find its own `static/` assets. Place the `.bat` file in the same folder and run it to open the dashboard at `http://127.0.0.1:8003`.

The dashboard's Whisper start action requires the original `transcription_server.py` file. That source file was not included in the supplied project artifacts and is explicitly listed in `Windows_Server/Whisper_STT/`.
