# Raspberry Pi application

This folder contains the supplied Pi application and the later alarm/session companion update.

From the project root on the Pi, create and activate a virtual environment, install the packages in `requirements.txt`, copy `.env.example` to `.env`, and set the same API token used by Windows. Download the Vosk and Piper model files into the paths listed in `models/README.md`.

Start the application from this folder with:

```bash
python3 main.py
```

The application expects the Nano on `/dev/ttyACM0` unless the port is changed in `hardware/nano_controller.py`. The remote endpoints default to the Tailscale hostname `monday-server`.
