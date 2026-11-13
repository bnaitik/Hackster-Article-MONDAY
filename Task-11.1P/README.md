# Monday: Smart Desk Bot

Monday is a smart desktop assistant built using Raspberry Pi 4 and Arduino Nano 33 IoT. It combines voice interaction, a visual display, environmental sensing, local AI processing and a web-based task management system.

The aim of this project is to create a compact desktop companion that can respond to voice commands, answer questions, manage tasks and interact with its surrounding environment.

## Features

- Voice interaction with wake-word detection
- AI-powered conversational assistant
- Animated eyes and digital clock display
- Speech recognition and voice responses
- Ambient light detection using BH1750
- Automatic LED brightness control
- Physical push-button input
- Task and reminder management
- Communication between Raspberry Pi and Arduino
- Locally hosted AI and backend services

## Hardware

- Raspberry Pi 4 (4GB RAM)
- 1TB SSD
- Arduino Nano 33 IoT
- 2.4-inch ILI9341 TFT LCD
- BH1750 light sensor
- LED and MOSFET module
- Push button
- Microphone and speaker

## Project Structure

### Arduino

Contains the firmware for the Arduino Nano 33 IoT. It handles sensor readings, LED control, button input and serial communication with the Raspberry Pi.

### Raspberry Pi

Contains the main application responsible for the display, voice assistant, audio processing and communication with the Arduino and Windows server.

### Windows Server

Contains the AI backend, task management tools and supporting services. Ollama is used to run the language model locally.

### Website

Contains the web interface and backend used for managing tasks and reminders.

## Software and Technologies

- Python
- Arduino IDE
- Raspberry Pi OS
- FastAPI
- Ollama
- Qwen language model
- Whisper
- Vosk
- Piper
- SQLite
- HTML, CSS and JavaScript
- Git and GitHub

## Getting Started

1. Download or clone this repository.
2. Set up the Arduino firmware and upload it to the Nano 33 IoT.
3. Configure the Raspberry Pi and install the required dependencies.
4. Set up the Windows server and install its required Python packages.
5. Install and configure Ollama with the selected language model.
6. Configure the network connection between the Raspberry Pi and Windows computer.
7. Start the required services and run the main application.

Refer to the individual project folders for their respective source files and configuration requirements.

## Important

The Windows AI server and Raspberry Pi application require their respective environment variables and dependencies to be configured before running.

The Whisper speech recognition service requires its server-side implementation and dependencies to be installed separately.

Do not upload private API tokens, passwords, personal configuration files or database files containing personal information to a public repository.

## Project Objective

Monday was developed as an embedded systems project to explore the integration of microcontrollers, single-board computers, sensors, networking, local AI and web technologies into one working device.

The project demonstrates how hardware and software components can communicate and work together to create an interactive desktop assistant.