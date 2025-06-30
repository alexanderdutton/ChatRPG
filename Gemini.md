# Gemini Project Configuration

## Project Overview
This project is a ChatRPG (Chat Role-Playing Game) with a Python backend and an HTML/JavaScript frontend. The goal is to create an interactive text-based adventure game.

## Technologies
- **Backend:** Python (FastAPI, Uvicorn)
- **Frontend:** HTML, CSS, JavaScript

## Project Structure
- `backend/`: Contains the Python backend server code, game logic, and API endpoints.
- `frontend/`: Contains the HTML, CSS, and JavaScript for the web-based user interface.

## Commands

### To start the Backend Server:
1. Navigate to the `backend` directory: `cd backend`
2. Activate the virtual environment: `venv\Scripts\activate` (Windows) or `source venv/bin/activate` (Linux/macOS)
3. Install dependencies (if not already installed): `pip install -r requirements.txt`
4. Run the server: `uvicorn main:app --reload`

### To start the Frontend Server:
1. Open the `frontend/index.html` file in your web browser. (No dedicated server needed for simple HTML files)

## Known Issues
- The game is in early development, and the map is very small.

## Gemini Integration Goals
- **NPC Dialogue:** Gemini should be used to generate dialogue when a player directly submits a message to a character.
- **Character Portraits:** Gemini should be used to generate character portraits only when a portrait does not already exist in the `frontend/portraits/` folder. Generated portraits should be saved to this folder to avoid regenerating them.

## Quality Standards
- **Linting:** Use a linter (e.g., `ruff` for Python, `ESLint` for JavaScript) to maintain code style and catch potential errors. Run linting checks before committing changes.